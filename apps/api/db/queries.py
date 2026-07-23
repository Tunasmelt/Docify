from datetime import datetime, timezone

from services.chunker import Chunk
from services.embedder import Vector
from services.parser import ParsedElement


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
    client.table("documents").update({"status": "parsing"}).eq("id", document_id).execute()


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
    vectors: list[Vector],
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
    """
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
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
                "embedding": vector,
                "metadata": {
                    "page_numbers": chunk.page_numbers,
                    "source_element_indices": chunk.source_element_indices,
                    "association_method": chunk.association_method,
                    "merged_caption_ids": chunk.merged_caption_ids,
                    "split_from_element_id": chunk.split_from_element_id,
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
