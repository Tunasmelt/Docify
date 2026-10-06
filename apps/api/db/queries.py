from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from services.embedder import EmbeddedChunk

# 2026-08-01 (FEAT-027): Chunk/ParsedElement are used ONLY as type hints in
# build_chunk_rows()'s signature below — never accessed for any attribute or
# logic at runtime in this module. A plain `from services.chunker import
# Chunk` pulled services.chunker -> services.parser -> (formerly) docling
# into every route that imports db.queries at all, including /health,
# /documents, /query — none of which ever touch parsing. `from __future__
# import annotations` makes every annotation in this module a string at
# runtime (PEP 563), so these names never need to actually exist when the
# module runs; TYPE_CHECKING keeps the real import available for static
# type checkers/IDEs without ever executing it at import time. Confirmed via
# a real isolation test (tests/test_parser_rewrite.py) that services.parser
# is not in sys.modules after `import db.queries` alone.
if TYPE_CHECKING:
    from services.chunker import Chunk
    from services.document_model import ParsedElement


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_document(client, *, user_id: str, filename: str, storage_path: str, mime_type: str, size_bytes: int) -> dict:
    result = (
        client.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": filename,
                "storage_path": storage_path,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
            }
        )
        .execute()
    )
    return result.data[0]


# documents.status only models coarse phases (uploaded/parsing/embedded/
# ready/failed — .agent/SCHEMA.md's document_status enum). There's no
# dedicated "chunked" status between parsing and embedded, so parsed_at
# and page_count are stamped as milestones within the 'parsing' phase
# rather than driving their own status value.
def mark_parsing(client, document_id: str) -> None:
    # `error` cleared explicitly (2026-08-02, FEAT-024 follow-up): a
    # fresh document already has error=None, so this is a no-op for the
    # normal /ingest path — but POST /reindex/{document_id} calls this on
    # a document that may be sitting at status='failed' with a real error
    # string from its last attempt, and that stale message must not
    # linger once a fresh attempt is genuinely underway.
    client.table("documents").update({"status": "parsing", "error": None}).eq("id", document_id).execute()


def mark_parsed(client, document_id: str, *, page_count: int | None) -> None:
    client.table("documents").update({"page_count": page_count, "parsed_at": _now_iso()}).eq("id", document_id).execute()


def mark_embedded(client, document_id: str) -> None:
    client.table("documents").update({"status": "embedded", "embedded_at": _now_iso()}).eq("id", document_id).execute()


def mark_ready(client, document_id: str) -> None:
    client.table("documents").update({"status": "ready"}).eq("id", document_id).execute()


def mark_failed(client, document_id: str, *, error: str) -> None:
    client.table("documents").update({"status": "failed", "error": error}).eq("id", document_id).execute()


def build_chunk_rows(
    *,
    document_id: str,
    user_id: str,
    chunks: list[Chunk],
    embedded_chunks: list[EmbeddedChunk],
    figure_paths: dict[int, str],
    elements: list[ParsedElement],
) -> list[dict]:
    """Maps chunker.py's rich, multi-element-aware Chunk objects onto
    chunks table rows. The schema's page_number/bbox columns are
    singular (one value per row), but a Chunk can span several source
    elements (grouped text, or a table+caption pair) — page_number takes
    the earliest page the chunk touches, and bbox is the first source
    element's own bbox (always the table/figure itself for TABLE/FIGURE
    chunks, or the first grouped element for TEXT/HEADING/LIST/CAPTION
    chunks). The full page list and provenance (source element indices,
    caption association) are never dropped — they go into metadata.

    embedding_provider (2026-07-31) is stamped per-row from each
    EmbeddedChunk, not a single document-wide value — embedder.py's
    Gemini fallback can tag only SOME of a document's chunks differently
    (a mid-batch Voyage RPM exhaustion, not a clean whole-document
    failure), so this must stay per-chunk to match. See .agent/MEMORY.md
    before assuming any two chunks' embeddings are comparable regardless
    of this column.
    """
    rows = []
    for chunk, embedded in zip(chunks, embedded_chunks, strict=True):
        source_element = elements[chunk.source_element_indices[0]]
        bbox = source_element.bbox
        rows.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk.chunk_index,
                "element_type": chunk.element_type.value,
                "page_number": min(chunk.page_numbers),
                "bbox": {"x0": bbox.x0, "y0": bbox.y0, "x1": bbox.x1, "y1": bbox.y1},
                "content": chunk.content,
                "figure_path": figure_paths.get(chunk.chunk_index),
                "embedding": embedded.vector,
                "embedding_provider": embedded.provider,
                "metadata": {
                    "page_numbers": chunk.page_numbers,
                    "source_element_indices": chunk.source_element_indices,
                    "association_method": chunk.association_method,
                    "merged_caption_ids": chunk.merged_caption_ids,
                    "split_from_element_id": chunk.split_from_element_id,
                    "section_heading": chunk.section_heading,
                },
            }
        )
    return rows


def insert_chunks(client, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    result = client.table("chunks").insert(rows).execute()
    return result.data


def delete_chunks_for_document(client, document_id: str) -> None:
    client.table("chunks").delete().eq("document_id", document_id).execute()


DOCUMENT_RESPONSE_COLUMNS = "id,filename,page_count,status,error,created_at,parsed_at,embedded_at"


def get_document(client, *, document_id: str, user_id: str) -> dict | None:
    """Scoped to user_id in the query itself, not checked after fetching
    by id alone — a document that doesn't exist and a document that
    belongs to someone else both produce the same empty result here, so
    callers can return an identical 404 for both without having to
    remember to hide the distinction themselves (API_CONTRACT.md: same
    response either way)."""
    rows = client.table("documents").select(DOCUMENT_RESPONSE_COLUMNS).eq("id", document_id).eq("user_id", user_id).execute().data
    return rows[0] if rows else None


def get_document_for_reindex(client, *, document_id: str, user_id: str) -> dict | None:
    """Separate from get_document()/DOCUMENT_RESPONSE_COLUMNS deliberately
    — POST /reindex/{document_id} (routes/ingest.py) needs storage_path
    (to re-download the already-uploaded file) and created_at (to echo
    back in the response), neither of which belongs in the public
    DocumentResponse shape get_document() serves to GET /documents/{id}.
    Scoped to user_id in the query itself, same discipline as
    get_document() — a document that doesn't exist and one that belongs
    to someone else both produce the same empty result, so the caller
    can return an identical 404 for both."""
    rows = (
        client.table("documents")
        .select("id,status,storage_path,created_at")
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def reap_stale_documents(client, *, user_id: str, threshold_seconds: int) -> list[str]:
    """Marks any of this user's documents still stuck in 'parsing' or
    'embedded' well past a reasonable processing time as 'failed', with
    an honest, specific error message — the real, lazy alternative to a
    worker-queue/scheduler this project doesn't have (routes/documents.py's
    GET /documents calls this before returning results; see
    STUCK_DOCUMENT_THRESHOLD_SECONDS in routes/ingest.py for the threshold
    and its justification, and .agent/SCOPE.md for why this reactive
    approach is an accepted tradeoff rather than a real job-queue fix).

    Two separate status-specific checks, not one: 'parsing' has no
    dedicated "started" timestamp of its own (created_at doubles as the
    reference point, since parsing begins immediately after creation in
    the real pipeline), while 'embedded' has a real, reliably-set
    embedded_at (mark_embedded, routes/ingest.py) that's a tighter, more
    accurate reference than created_at would be for a document that
    already made it that far. Scoped to user_id — this fires
    opportunistically from one user's own GET /documents call and has no
    reason to touch any other user's rows.

    Returns the ids of every document actually reaped (for logging);
    empty list if nothing was stale.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    error_message = "processing timed out, possibly interrupted by a service restart"

    reaped_ids: list[str] = []

    stuck_parsing = (
        client.table("documents")
        .update({"status": "failed", "error": error_message})
        .eq("user_id", user_id)
        .eq("status", "parsing")
        .lt("created_at", cutoff)
        .execute()
    )
    reaped_ids.extend(row["id"] for row in stuck_parsing.data)

    stuck_embedded = (
        client.table("documents")
        .update({"status": "failed", "error": error_message})
        .eq("user_id", user_id)
        .eq("status", "embedded")
        .lt("embedded_at", cutoff)
        .execute()
    )
    reaped_ids.extend(row["id"] for row in stuck_embedded.data)

    return reaped_ids


def list_documents(
    client, *, user_id: str, status: str | None, limit: int, cursor_created_at: str | None
) -> list[dict]:
    """Keyset pagination on created_at desc (documents_user_idx already
    indexes (user_id, created_at desc) — see SCHEMA.md). Fetches
    limit + 1 rows so the caller can tell whether another page exists
    without a separate count query; the +1 row itself is never returned
    to the caller.

    Cursor is created_at alone, not (created_at, id) — a real keyset
    tiebreaker would need a compound OR filter. Two documents sharing an
    identical microsecond-precision created_at is not a realistic
    scenario for how documents get created here (each insert is a
    separate sequential API call), so this is a documented, accepted
    simplification, not an oversight.
    """
    query = client.table("documents").select(DOCUMENT_RESPONSE_COLUMNS).eq("user_id", user_id)
    if status is not None:
        query = query.eq("status", status)
    if cursor_created_at is not None:
        query = query.lt("created_at", cursor_created_at)
    return query.order("created_at", desc=True).limit(limit + 1).execute().data


def list_figure_paths_for_document(client, document_id: str) -> list[str]:
    rows = (
        client.table("chunks")
        .select("figure_path")
        .eq("document_id", document_id)
        .not_.is_("figure_path", "null")
        .execute()
        .data
    )
    return [r["figure_path"] for r in rows]


def delete_document(client, document_id: str) -> None:
    """Deletes the documents row. chunks and citations cascade at the DB
    level (`on delete cascade` FKs — SCHEMA.md); Storage objects and
    conversation.document_ids array references do NOT (arrays aren't
    FK-cascadable in Postgres), so callers must clean those up
    separately — see routes/documents.py's delete_document handler,
    which does so before calling this."""
    client.table("documents").delete().eq("id", document_id).execute()


def remove_document_from_conversations(client, *, document_id: str, user_id: str) -> None:
    """document_ids is a plain array column, not a foreign key, so
    Postgres never cascade-cleans it on its own (API_CONTRACT.md still
    calls this out as something DELETE /documents/{id} must do). Scoped
    to user_id defensively, even though a conversation referencing
    another user's document_id shouldn't be possible once Phase 2 exists
    — costs nothing to also filter here."""
    conversations = (
        client.table("conversations")
        .select("id,document_ids")
        .eq("user_id", user_id)
        .cs("document_ids", [document_id])
        .execute()
        .data
    )
    for conversation in conversations:
        remaining = [d for d in conversation["document_ids"] if d != document_id]
        client.table("conversations").update({"document_ids": remaining}).eq("id", conversation["id"]).execute()


def documents_owned_by_user(client, *, document_ids: list[str], user_id: str) -> set[str]:
    """Returns the subset of `document_ids` that actually belong to
    `user_id`. A caller comparing this against the full requested set
    gets an identical result whether a given id doesn't exist at all or
    belongs to someone else — same "don't give an attacker an oracle"
    discipline as get_document()'s 404 handling (API_CONTRACT.md)."""
    if not document_ids:
        return set()
    rows = client.table("documents").select("id").in_("id", document_ids).eq("user_id", user_id).execute().data
    return {row["id"] for row in rows}


def get_conversation(client, *, conversation_id: str, user_id: str) -> dict | None:
    """Scoped to user_id in the query itself — same pattern as
    get_document(). /query pre-checks conversation ownership with this
    before calling create_query_turn() so an invalid conversation_id
    gets a clean 404 without needing to inspect a Postgres exception
    raised from inside the RPC function."""
    rows = client.table("conversations").select("id,document_ids").eq("id", conversation_id).eq("user_id", user_id).execute().data
    return rows[0] if rows else None


def create_query_turn(
    client,
    *,
    user_id: str,
    conversation_id: str | None,
    document_ids: list[str],
    question: str,
    answer_content: str,
    answer_raw_content: str,
    retrieved_chunk_ids: list[str],
    answer_metadata: dict,
    citations: list[dict],
) -> dict:
    """Wraps the create_query_turn RPC (migrations/20260724_002) — the
    single atomic write for a /query turn: the conversation (if new),
    both the user-question and assistant-answer message rows, and every
    citation row (all verdicts, including unsupported — full audit
    trail; /query itself filters the response, this stores everything).
    Raises (via the underlying postgrest/RPC client) if the function
    itself raises — conversation_id is expected to already be verified
    as belonging to user_id by this point (get_conversation() above),
    so that should only happen on a genuine race condition, not a normal
    client error."""
    result = (
        client.rpc(
            "create_query_turn",
            {
                "p_user_id": user_id,
                "p_conversation_id": conversation_id,
                "p_document_ids": document_ids,
                "p_question": question,
                "p_answer_content": answer_content,
                "p_answer_raw_content": answer_raw_content,
                "p_retrieved_chunk_ids": retrieved_chunk_ids,
                "p_answer_metadata": answer_metadata,
                "p_citations": citations,
            },
        )
        .execute()
        .data
    )
    return result[0]


# FEAT-026: GET /conversations, GET /conversations/{id}/messages

CONVERSATION_LIST_COLUMNS = "id,title,document_ids,updated_at,messages(count)"


def list_conversations(client, *, user_id: str, limit: int, cursor_updated_at: str | None) -> list[dict]:
    """Keyset pagination on updated_at desc (conversations_user_idx already
    indexes (user_id, updated_at desc) — SCHEMA.md), same +1-row
    has-more-page pattern as list_documents(). message_count comes from
    PostgREST's embedded count aggregate (`messages(count)`) rather than a
    second per-conversation query — confirmed live this returns each row
    shaped as `{..., "messages": [{"count": N}]}`; the caller
    (routes/conversations.py) flattens that, since Pydantic can't consume
    the nested shape directly as a plain int field."""
    query = client.table("conversations").select(CONVERSATION_LIST_COLUMNS).eq("user_id", user_id)
    if cursor_updated_at is not None:
        query = query.lt("updated_at", cursor_updated_at)
    return query.order("updated_at", desc=True).limit(limit + 1).execute().data


def get_conversation_detail(client, *, conversation_id: str, user_id: str) -> dict | None:
    """Scoped to user_id in the query itself — same pattern as
    get_document()/get_conversation(). Fuller column set than
    get_conversation() (id,document_ids only — used for /query's
    ownership pre-check) since this backs GET /conversations/{id}/messages'
    `conversation` object, which also needs title/created_at/updated_at."""
    rows = (
        client.table("conversations")
        .select("id,title,document_ids,created_at,updated_at")
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def rename_conversation(client, *, conversation_id: str, user_id: str, title: str) -> dict | None:
    """Scoped to user_id in the .eq() filter itself (not checked after the
    fact) — same "doesn't exist and belongs to someone else look
    identical" discipline as get_document()/get_conversation(). Returns
    None for either case so POST /conversations/{id}/rename can return an
    identical 404 without a separate ownership check.

    Deliberately does NOT bump updated_at — a rename is a metadata edit,
    not new conversation activity, and conversations are sorted by
    updated_at desc (SCHEMA.md's conversations_user_idx) for GET
    /conversations. Bumping it here would jump a renamed-but-otherwise-
    untouched conversation to the top of "Recent," which nothing about
    a rename action implies the user wants."""
    rows = (
        client.table("conversations")
        .update({"title": title})
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def delete_conversation(client, *, conversation_id: str, user_id: str) -> bool:
    """Scoped to user_id in the .eq() filter itself, same discipline as
    every other owned-row lookup in this file. messages and citations
    cascade at the DB level (`on delete cascade` FKs, SCHEMA.md) — unlike
    delete_document(), there is no Storage cleanup or array-reference
    cleanup needed here, since a conversation owns no Storage objects and
    nothing else references a conversation_id the way documents.id is
    referenced by conversations.document_ids.

    Returns whether a row was actually deleted, so the caller can return
    a real 404 for a conversation that doesn't exist or belongs to
    someone else, rather than a silent no-op 204."""
    rows = (
        client.table("conversations")
        .delete()
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return bool(rows)


def list_messages_for_conversation(
    client, *, conversation_id: str, user_id: str, limit: int | None = None
) -> list[dict]:
    """Ordered oldest-first (messages_conv_idx already indexes
    (conversation_id, created_at) ascending — SCHEMA.md), matching a
    conversation's natural reading order. conversation_id ownership is
    already verified by the caller via get_conversation_detail() before
    this ever runs (same check-once-then-trust-the-id pattern
    create_query_turn's RPC uses) — the user_id filter here is
    defense-in-depth on top of that, not the primary check, same
    "costs nothing to also filter" reasoning as
    remove_document_from_conversations().

    limit=None (default, GET /conversations/{id}/messages's own usage,
    unchanged): fetch every message, oldest-first, as before.
    limit=N (routes/query.py's 2026-07-27 conversation-memory follow-up):
    same query, same oldest-first order, then keep just the last N in
    Python. Deliberately NOT `order(created_at desc).limit(N)` reversed
    back — confirmed empirically that this breaks: a turn's user and
    assistant messages are inserted in the same create_query_turn()
    transaction, and Postgres's `now()` is transaction-start time, so
    both rows share an IDENTICAL created_at. A DESC query does not
    reliably invert the ASC order for tied rows (observed returning
    user-before-assistant even under DESC), so reversing it silently
    swapped a turn's two messages. Reusing the exact same proven ASC
    query and slicing client-side avoids this correctness risk entirely
    — the accepted tradeoff is fetching the full (but
    conversation_id-indexed, so still a cheap index scan, not a table
    scan) history rather than only the tail, judged fine at this
    project's portfolio scale rather than assumed free."""
    rows = (
        client.table("messages")
        .select("id,role,content,created_at")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    if limit is None:
        return rows
    return rows[-limit:] if limit > 0 else []


CITATION_JOIN_COLUMNS = (
    "id,message_id,marker,verdict,supporting_quote,chunk_id,"
    "chunks(document_id,page_number,element_type,content,figure_path,documents(filename,mime_type))"
)


def list_citations_for_messages(client, *, message_ids: list[str], user_id: str) -> dict[str, list[dict]]:
    """Citations for a batch of message ids in one query, grouped by
    message_id — avoids an N+1 query per message. Filters out only
    'unsupported' verdicts (not an allowlist of the other three) to
    match exactly what /query's own response (and therefore each
    message's stored `content` text — see routes/query.py's
    _strip_dropped_markers) actually contains; 'unsupported' citations
    ARE persisted (create_query_turn's own full-audit-trail comment) but
    were never meant to be user-facing. 'unverified' rows (2026-08-03 —
    verification genuinely could not run) are NOT filtered here, same as
    live /query responses — they're real, kept-not-dropped citations,
    just ones the client renders with their own distinct indicator.
    Ordered by marker so a message's citations come back in the same
    order they appear inline in its content text."""
    if not message_ids:
        return {}
    rows = (
        client.table("citations")
        .select(CITATION_JOIN_COLUMNS)
        .in_("message_id", message_ids)
        .eq("user_id", user_id)
        .neq("verdict", "unsupported")
        .order("marker")
        .execute()
        .data
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(row)
    return grouped


# Settings batch 3, part 1: GET /export/conversations (routes/export.py).
# Deliberately a SEPARATE set of queries from the ones above, not a
# reuse of list_conversations()/list_messages_for_conversation()/
# list_citations_for_messages() with different args bolted on — those
# three are each shaped for a single bounded view (one page, one
# conversation, one batch of message ids) and one of them
# (list_citations_for_messages) actively filters 'unsupported' verdicts
# to match what a live chat turn ever showed. An export's job is the
# opposite: everything this user has, unfiltered, in one pass — reusing
# the live-view queries and trying to bolt a "no really, ALL of it"
# flag onto them would leave the underlying filtering assumption sitting
# right next to code that must never apply it, an easy place for a
# future edit to silently reintroduce it. Separate functions make the
# completeness guarantee the export route depends on impossible to
# accidentally weaken by editing the wrong shared function.


def list_all_conversations_for_export(client, *, user_id: str) -> list[dict]:
    """Every conversation belonging to user_id, oldest first — no
    pagination, unlike list_conversations() (GET /conversations' own
    keyset-paginated listing). An export exists specifically to be
    complete; a page-limited fetch here would silently truncate past
    whatever page size was chosen, exactly the failure mode this
    feature exists to prevent. Unbounded is an accepted, stated choice
    at this project's real portfolio/demo scale (.agent/SCOPE.md) — not
    something to revisit without real evidence usage has grown past it."""
    return (
        client.table("conversations")
        .select("id,title,document_ids,created_at,updated_at")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
        .data
    )


def list_all_messages_for_user(client, *, user_id: str) -> list[dict]:
    """Every message across every one of user_id's conversations, in one
    query — avoids an N+1 fetch per conversation. Includes raw_content
    (list_messages_for_conversation(), the live-view query, never
    needed it): routes/export.py's Markdown rendering deliberately uses
    raw_content instead of the UI-facing content for assistant messages
    — see that module for why."""
    return (
        client.table("messages")
        .select("id,conversation_id,role,content,raw_content,created_at")
        .eq("user_id", user_id)
        .order("conversation_id")
        .order("created_at")
        .execute()
        .data
    )


EXPORT_CITATION_COLUMNS = (
    "id,message_id,marker,claim_span,claim_start,claim_end,verdict,supporting_quote,"
    "verifier_model,verified_at,chunk_id,"
    "chunks(document_id,page_number,element_type,documents(filename))"
)


def list_all_citations_for_export(client, *, user_id: str) -> list[dict]:
    """Every citation belonging to user_id — deliberately UNFILTERED,
    including 'unsupported' verdicts that list_citations_for_messages()
    (the live-view query) drops to match what a chat turn's own
    response ever showed on screen. A citation the app silently
    dropped from the visible answer is still part of this user's real,
    persisted audit trail (create_query_turn's own "full audit trail —
    /query itself filters" comment) and belongs in their export: this
    feature exists to give a user real confidence in what's about to be
    exported/deleted, and a completeness guarantee that quietly
    excludes some of their own citations would undermine exactly that."""
    return (
        client.table("citations")
        .select(EXPORT_CITATION_COLUMNS)
        .eq("user_id", user_id)
        .order("marker")
        .execute()
        .data
    )


def get_document_filenames(client, *, document_ids: list[str], user_id: str) -> dict[str, str]:
    """id -> filename map, scoped to user_id. Used only to LABEL a
    conversation's document_ids / a citation's source with a real,
    readable name — never to include document CONTENT, which is
    explicitly out of scope for this export (routes/export.py's module
    docstring has the full reasoning)."""
    if not document_ids:
        return {}
    rows = (
        client.table("documents")
        .select("id,filename")
        .in_("id", document_ids)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return {row["id"]: row["filename"] for row in rows}
