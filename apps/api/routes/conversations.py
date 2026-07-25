import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.conversations import (
    ConversationDetail,
    ConversationListResponse,
    ConversationMessagesResponse,
    ConversationResponse,
    MessageResponse,
)
from models.query import CitationResponse
from routes._pagination import decode_cursor, encode_cursor
from services.figure_fetcher import signed_figure_url

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
):
    cursor_updated_at = None
    if cursor is not None:
        try:
            cursor_updated_at = decode_cursor(cursor)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "invalid cursor"))

    user_id = request.state.user_id
    client = get_service_role_client()
    rows = queries.list_conversations(client, user_id=user_id, limit=limit, cursor_updated_at=cursor_updated_at)

    # Same +1-row has-more-page pattern as GET /documents (FEAT-008).
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1]["updated_at"]) if has_more and page else None

    conversations = [
        ConversationResponse(
            id=row["id"],
            title=row["title"],
            document_ids=row["document_ids"],
            # PostgREST's embedded count comes back nested as
            # {"messages": [{"count": N}]} (confirmed live against the
            # real local stack — db/queries.py's list_conversations()),
            # not a plain int — flattened here since Pydantic can't
            # consume that shape directly as ConversationResponse.message_count.
            message_count=row["messages"][0]["count"] if row["messages"] else 0,
            updated_at=row["updated_at"],
        )
        for row in page
    ]
    return ConversationListResponse(conversations=conversations, next_cursor=next_cursor)


def _citation_response(client, row: dict) -> CitationResponse:
    """Builds a CitationResponse from a joined citations+chunks+documents
    row (db/queries.py's CITATION_JOIN_COLUMNS shape) — same fields,
    same figure_url mechanism (services.figure_fetcher.signed_figure_url)
    as routes/query.py's live citations, so a citation looks identical
    whether it just came back from POST /query or is being read back
    later from history."""
    chunk = row["chunks"]
    figure_url = None
    if chunk["element_type"] == "figure" and chunk["figure_path"]:
        figure_url = signed_figure_url(client, chunk["figure_path"])

    return CitationResponse(
        marker=row["marker"],
        chunk_id=row["chunk_id"],
        document_id=chunk["document_id"],
        document_name=chunk["documents"]["filename"],
        page_number=chunk["page_number"],
        element_type=chunk["element_type"],
        snippet=chunk["content"][:200],
        verdict=row["verdict"],
        supporting_quote=row["supporting_quote"],
        figure_url=figure_url,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
    response_model_exclude_none=True,
)
async def get_conversation_messages(conversation_id: str, request: Request):
    user_id = request.state.user_id
    client = get_service_role_client()

    conversation_row = queries.get_conversation_detail(client, conversation_id=conversation_id, user_id=user_id)
    if conversation_row is None:
        # Same discipline as get_document()'s 404 (API_CONTRACT.md): a
        # conversation that doesn't exist and one that belongs to another
        # user produce the identical response — get_conversation_detail()
        # scopes user_id in the query itself, nothing to leak here.
        return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "conversation not found"))

    message_rows = queries.list_messages_for_conversation(client, conversation_id=conversation_id, user_id=user_id)
    message_ids = [m["id"] for m in message_rows]
    citations_by_message = queries.list_citations_for_messages(client, message_ids=message_ids, user_id=user_id)

    messages = [
        MessageResponse(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            created_at=m["created_at"],
            # None (omitted from the response) for user messages — a
            # question never has citations. Assistant messages always get
            # a list, even if empty (e.g. the "couldn't find relevant
            # information" no-retrieval case in routes/query.py), matching
            # that role's own always-has-citations shape in POST /query.
            citations=(
                [_citation_response(client, c) for c in citations_by_message.get(m["id"], [])]
                if m["role"] == "assistant"
                else None
            ),
        )
        for m in message_rows
    ]

    return ConversationMessagesResponse(
        conversation=ConversationDetail(**conversation_row),
        messages=messages,
    )
