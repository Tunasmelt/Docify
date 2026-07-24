import base64
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.documents import DocumentListResponse, DocumentResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Matches .agent/SCHEMA.md's document_status enum exactly. Validated here
# so an invalid ?status= value gets a clean 422 instead of surfacing a
# raw Postgres "invalid input value for enum document_status" error —
# PostgREST would otherwise try to cast the string straight into the
# enum column and let Postgres reject it.
_VALID_STATUSES = {"uploaded", "parsing", "embedded", "ready", "failed"}


def _encode_cursor(created_at: str) -> str:
    return base64.urlsafe_b64encode(created_at.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor.encode()).decode()


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
):
    if status is not None and status not in _VALID_STATUSES:
        return JSONResponse(
            status_code=422,
            content=error_envelope("VALIDATION_ERROR", f"invalid status {status!r}"),
        )

    cursor_created_at = None
    if cursor is not None:
        try:
            cursor_created_at = _decode_cursor(cursor)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "invalid cursor"))

    user_id = request.state.user_id
    client = get_service_role_client()
    rows = queries.list_documents(client, user_id=user_id, status=status, limit=limit, cursor_created_at=cursor_created_at)

    # Fetched limit + 1 to detect whether another page exists without a
    # separate count query — the probe row itself is never returned.
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]["created_at"]) if has_more and page else None

    return DocumentListResponse(documents=[DocumentResponse(**row) for row in page], next_cursor=next_cursor)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str, request: Request):
    user_id = request.state.user_id
    client = get_service_role_client()

    row = queries.get_document(client, document_id=document_id, user_id=user_id)
    if row is None:
        # Same response whether document_id doesn't exist at all or
        # belongs to another user — get_document() scopes user_id in the
        # query itself, so there's nothing here to accidentally leak
        # (API_CONTRACT.md; same discipline as FEAT-007's storage_path fix).
        return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "document not found"))

    return DocumentResponse(**row)


def _storage_deletion_failed(document_id: str, user_id: str, *, bucket: str) -> JSONResponse:
    """Logs only document_id/user_id/bucket — never the exception's own
    message or a traceback (exc_info), since a Storage error's message
    isn't guaranteed not to embed the object path itself. Same coarse-
    reason-over-raw-detail discipline as FEAT-007's StoragePathError
    logging fix (.agent/MEMORY.md). The document row and any chunks are
    provably untouched at this point — the response says so because a
    caller getting a bare 500 has no way to know that on its own."""
    logger.error(
        "delete_document: Storage removal failed for document %s (user %s), bucket=%s",
        document_id,
        user_id,
        bucket,
    )
    return JSONResponse(
        status_code=500,
        content=error_envelope(
            "STORAGE_ERROR",
            "failed to delete document storage — the document was not modified, retrying is safe",
        ),
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, request: Request):
    user_id = request.state.user_id
    client = get_service_role_client()

    rows = (
        client.table("documents")
        .select("id,status,storage_path")
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    if not rows:
        return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "document not found"))
    document = rows[0]

    if document["status"] in ("parsing", "embedded"):
        # Both statuses still have a real in-flight background task
        # (routes/ingest.py's run_ingest_pipeline): 'parsing' covers the
        # download-through-embed stages, and 'embedded' covers figure
        # upload + the bulk chunks insert + mark_ready, which all happen
        # strictly after mark_embedded() sets this status. Deleting the
        # documents row out from under either window lets that still-
        # running task's later insert_chunks() call fail with a dangling
        # chunks_document_id_fkey violation — confirmed live during
        # FEAT-014's UI wiring pass (.agent/GAPS.md).
        return JSONResponse(
            status_code=409,
            content=error_envelope("CONFLICT", "document is currently being processed"),
        )

    # Figure paths must be read before delete_document() below — chunks
    # (and their figure_path values) cascade-delete with the documents
    # row, so this is the last point they're queryable.
    figure_paths = queries.list_figure_paths_for_document(client, document_id)

    # Storage cleanup happens before the DB delete, deliberately: if a
    # Storage call genuinely fails, the document row is still intact —
    # better than a split-brain state where the DB row is gone but
    # Storage objects are orphaned with no record of which document they
    # belonged to. storage_path is "uploads/{user_id}/{filename}" —
    # .from_("uploads") already scopes to that bucket (same prefix-strip
    # as run_ingest_pipeline's download).
    #
    # Self-verification (2026-07-23) proved this retry-safe live: a
    # simulated failure on the second remove() call left the document
    # row and chunks fully intact, and a follow-up DELETE completed
    # cleanly — remove() on an already-gone object doesn't itself error.
    # What that same check found missing was error handling here at
    # all: an unhandled Storage exception previously surfaced as a bare
    # 500 with no envelope and no log line. Both remove() calls are now
    # wrapped individually so the failing bucket can be identified.
    in_bucket_path = document["storage_path"].removeprefix("uploads/")
    try:
        client.storage.from_("uploads").remove([in_bucket_path])
    except Exception:
        return _storage_deletion_failed(document_id, user_id, bucket="uploads")

    if figure_paths:
        try:
            client.storage.from_("figures").remove(figure_paths)
        except Exception:
            return _storage_deletion_failed(document_id, user_id, bucket="figures")

    # document_ids is a plain array column, not a foreign key — Postgres
    # never cascades this on its own (API_CONTRACT.md still requires it).
    queries.remove_document_from_conversations(client, document_id=document_id, user_id=user_id)

    # chunks and citations cascade at the DB level (`on delete cascade`
    # FKs — SCHEMA.md).
    queries.delete_document(client, document_id)

    return Response(status_code=204)
