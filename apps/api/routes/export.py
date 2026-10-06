import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from db import queries
from db.client import get_service_role_client
from models.export import ExportCitation, ExportConversation, ExportMessage, ExportPayload

logger = logging.getLogger(__name__)

router = APIRouter()

# Settings batch 3, part 1 — export all of a user's CONVERSATIONAL data
# (conversations, messages, citations). Deliberately does NOT include
# documents themselves: those are large binary files (PDFs, DOCX, PPTX)
# with their own existing lifecycle (upload, re-index, delete) — bundling
# gigabytes of source PDFs into a JSON/Markdown export would turn a
# lightweight "here's your data" download into a real storage/bandwidth
# problem, and a user who wants their original files back already has
# them (they uploaded them) or can re-download via a future dedicated
# document-export path if that's ever built. What genuinely can't be
# reconstructed by the user themselves is the CONVERSATION history and
# the verification audit trail (claims, quotes, verdicts) — that's the
# real, non-recoverable data this exists to protect before batch 3's
# second half (account deletion) makes losing it permanent. Stated here
# explicitly rather than silently narrowing scope: a reviewer or future
# maintainer should be able to see this was a deliberate line, not an
# oversight.
#
# New module, not an addition to routes/conversations.py or a new
# routes/settings.py: this isn't really a "conversations" action (it
# spans every conversation a user has, not one addressed by id, and its
# job is fundamentally different — a data-portability guarantee, not
# CRUD on a conversation resource) and there's no existing settings.py
# to extend (Settings batches 1/2 are pure Supabase-Auth-client-side or
# live in models/routes "query.py" for preference params — neither
# precedent fits an export endpoint). Matches this project's existing
# one-file-per-concern convention (ingest.py, documents.py, query.py,
# conversations.py) — .agent/API_CONTRACT.md's stale "Not-yet-defined"
# stub (`GET /conversations/{id}/export`) anticipated a narrower,
# single-conversation, Markdown-only shape; this implements a broader
# whole-account, both-formats export instead (matching the
# account-deletion-precursor framing this task was actually given), so
# that stub is removed rather than left half-matching a different design.
#
# No rate limiting: read-only, makes zero calls to Voyage/Gemini or any
# other paid vendor API — the exact same reasoning GET /conversations and
# GET /documents (both already unrated-limited) already rest on. Only
# /ingest and /query/-stream carry @limiter decorators in this codebase,
# specifically because those are the routes that draw on real, metered
# vendor quota (routes/query.py's own module comment). An export doing
# nothing but reading this user's own already-stored rows has the same
# real-cost profile as those two existing unlimited routes, not a
# different one.
#
# Synchronous response, not an async job + download-when-ready: this
# project's stated real scale is portfolio/demo, not thousands of
# conversations per user (.agent/SCOPE.md) — a single user's entire
# conversation history is a handful of indexed queries against tables
# already sized for interactive per-conversation reads elsewhere in this
# API, not a bulk/warehouse-scale operation. Building a job queue,
# status-polling endpoint, and download-link storage for a payload that
# resolves in well under a second at this project's real size would be
# real, unjustified complexity — the kind of speculative infrastructure
# this project's own established discipline (.agent/MEMORY.md's
# "ship minimal, measure before adding speculative complexity" pattern
# from reranking's rollout) argues against building ahead of evidence
# it's needed. Revisit if real usage ever shows this taking long enough
# to matter.


def _date_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _build_citation(row: dict) -> ExportCitation:
    chunk = row["chunks"]
    return ExportCitation(
        marker=row["marker"],
        chunk_id=row["chunk_id"],
        document_name=chunk["documents"]["filename"],
        page_number=chunk["page_number"],
        element_type=chunk["element_type"],
        claim_span=row["claim_span"],
        claim_start=row["claim_start"],
        claim_end=row["claim_end"],
        verdict=row["verdict"],
        supporting_quote=row["supporting_quote"],
        verifier_model=row["verifier_model"],
        verified_at=row["verified_at"],
    )


def _build_payload(client, user_id: str) -> ExportPayload:
    conversation_rows = queries.list_all_conversations_for_export(client, user_id=user_id)
    message_rows = queries.list_all_messages_for_user(client, user_id=user_id)
    citation_rows = queries.list_all_citations_for_export(client, user_id=user_id)

    all_document_ids = sorted({d for c in conversation_rows for d in c["document_ids"]})
    filenames = queries.get_document_filenames(client, document_ids=all_document_ids, user_id=user_id)

    citations_by_message: dict[str, list[dict]] = {}
    for row in citation_rows:
        citations_by_message.setdefault(row["message_id"], []).append(row)

    messages_by_conversation: dict[str, list[dict]] = {}
    for row in message_rows:
        messages_by_conversation.setdefault(row["conversation_id"], []).append(row)

    conversations: list[ExportConversation] = []
    for c in conversation_rows:
        messages = [
            ExportMessage(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                raw_content=m["raw_content"],
                created_at=m["created_at"],
                citations=[_build_citation(r) for r in citations_by_message.get(m["id"], [])],
            )
            for m in messages_by_conversation.get(c["id"], [])
        ]
        conversations.append(
            ExportConversation(
                id=c["id"],
                title=c["title"],
                # Falls back to the raw id for a document_id that somehow
                # has no matching row (deleted out from under an export
                # mid-request, or any other edge case) rather than
                # raising — an export must never hard-fail because one
                # cross-reference came up short; better to surface
                # something than lose the whole download.
                document_names=[filenames.get(d, d) for d in c["document_ids"]],
                created_at=c["created_at"],
                updated_at=c["updated_at"],
                messages=messages,
            )
        )

    return ExportPayload(
        exported_at=_now_iso(),
        conversation_count=len(conversations),
        message_count=sum(len(c.messages) for c in conversations),
        citation_count=sum(len(m.citations) for c in conversations for m in c.messages),
        conversations=conversations,
    )


# Markdown rendering — one file, one `##` section per conversation
# (a real choice, not a default: a multi-file zip would need real new
# zip-handling complexity this project has no other use for, for a
# benefit — separately downloadable per-conversation files — nothing in
# this task actually asked for; a single file is also simply easier for
# a user to skim/search/archive as one artifact).
#
# Deliberately uses raw_content (pre-verification-stripping) instead of
# the UI-facing `content` for assistant messages, and lists EVERY
# citation (including 'unsupported' ones the live app never showed) in
# each message's Sources block. This is a deliberate divergence from
# how the live chat UI renders the same turn: a personal-data export is
# not a chat replay — completeness (showing the user their own full
# audit trail, including where the system caught and rejected an
# unsupported claim) matters more here than visual fidelity to what the
# UI happened to display in the moment. Citations render as a
# `[N]`-marker + Sources-footnote pair, the same motif
# components/chat/citation-marker.tsx's own comment names as this app's
# established convention, not a new one invented for export.
def _render_markdown(payload: ExportPayload) -> str:
    lines: list[str] = []
    lines.append("# Docify Conversation Export")
    lines.append("")
    lines.append(
        f"Exported {payload.exported_at} · {payload.conversation_count} conversation"
        f"{'s' if payload.conversation_count != 1 else ''} · {payload.message_count} message"
        f"{'s' if payload.message_count != 1 else ''} · {payload.citation_count} citation"
        f"{'s' if payload.citation_count != 1 else ''}"
    )
    lines.append("")
    lines.append(
        "This export covers your conversations, messages, and citation verification "
        "records only. Source documents themselves are not included."
    )

    for conversation in payload.conversations:
        lines.append("")
        lines.append("---")
        lines.append("")
        title = conversation.title or "(untitled conversation)"
        lines.append(f"## {title}")
        docs = ", ".join(conversation.document_names) if conversation.document_names else "(none)"
        lines.append(f"_Documents: {docs}_")
        lines.append(f"_Created {conversation.created_at} · Last updated {conversation.updated_at}_")

        for message in conversation.messages:
            lines.append("")
            speaker = "You" if message.role == "user" else "Docify"
            lines.append(f"**{speaker}** _{message.created_at}_")
            lines.append("")
            text = message.raw_content if message.role == "assistant" and message.raw_content else message.content
            lines.append(text)

            if message.citations:
                lines.append("")
                lines.append("> **Sources**")
                for citation in message.citations:
                    quote = f' — "{citation.supporting_quote}"' if citation.supporting_quote else ""
                    lines.append(
                        f"> [{citation.marker}] {citation.document_name}, p. {citation.page_number} "
                        f"— **{citation.verdict}**{quote}"
                    )

    return "\n".join(lines) + "\n"


@router.get("/export/conversations")
def export_conversations(request: Request, format: str = Query("json", pattern="^(json|markdown)$")):
    user_id = request.state.user_id
    client = get_service_role_client()

    payload = _build_payload(client, user_id)

    if format == "markdown":
        body = _render_markdown(payload)
        media_type = "text/markdown; charset=utf-8"
        filename = f"docify-export-{_date_stamp()}.md"
    else:
        body = payload.model_dump_json(indent=2)
        media_type = "application/json"
        filename = f"docify-export-{_date_stamp()}.json"

    logger.info(
        "export_conversations: user %s exported %d conversations, %d messages, %d citations as %s",
        user_id,
        payload.conversation_count,
        payload.message_count,
        payload.citation_count,
        format,
    )

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
