import logging
from io import BytesIO

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.ingest import IngestRequest, IngestResponse
from services.chunker import Chunker
from services.embedder import Embedder
from services.parser import Parser

logger = logging.getLogger(__name__)

router = APIRouter()

# Phase 1 scope is PDF only — DOCX/PPTX/HTML are explicitly out of scope
# until Phase 4 (.agent/SCOPE.md).
SUPPORTED_MIME_TYPES = {"application/pdf"}


def get_pipeline_runner():
    """FastAPI dependency returning the background-pipeline callable.
    Indirection exists purely so tests can override it via
    `app.dependency_overrides` with a `functools.partial(run_ingest_pipeline,
    parser=..., chunker=..., embedder=...)` — real constructor injection
    into the real pipeline function, not a monkeypatch of it."""
    return run_ingest_pipeline


@router.post("/ingest", status_code=202, response_model=IngestResponse)
async def post_ingest(
    payload: IngestRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline_runner=Depends(get_pipeline_runner),
):
    user_id = request.state.user_id

    # storage_path prefix checked before anything else is created —
    # per API_CONTRACT.md and this task's explicit ordering requirement.
    # This is the synchronous 403 path (no document row created at all);
    # run_ingest_pipeline() below enforces the same invariant again on
    # its own, independent of this route being the only caller — see its
    # docstring.
    if not _storage_path_belongs_to_user(payload.storage_path, user_id):
        return JSONResponse(
            status_code=403,
            content=error_envelope("FORBIDDEN", "storage_path does not belong to the authenticated user"),
        )

    if payload.mime_type not in SUPPORTED_MIME_TYPES:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "VALIDATION_ERROR", f"unsupported mime_type {payload.mime_type!r} (Phase 1 supports PDF only)"
            ),
        )

    client = get_service_role_client()
    document = queries.create_document(
        client,
        user_id=user_id,
        filename=payload.filename,
        storage_path=payload.storage_path,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )

    background_tasks.add_task(
        pipeline_runner, document_id=document["id"], user_id=user_id, storage_path=payload.storage_path
    )

    return IngestResponse(document_id=document["id"], status=document["status"], created_at=document["created_at"])


def _storage_path_belongs_to_user(storage_path: str, user_id: str) -> bool:
    return storage_path.startswith(f"uploads/{user_id}/")


class IngestInvariantError(Exception):
    """Raised when run_ingest_pipeline's own precondition is violated —
    a storage_path that doesn't belong to the given user_id. post_ingest
    already checks this before ever scheduling the background task, but
    that only guarantees the invariant for calls that go through the
    route. Codex review (2026-07-23) flagged that "no current caller
    violates this" is not the same claim as "this is enforced" — see
    .agent/MEMORY.md — so this function checks its own precondition
    again rather than trusting its one caller today to stay its only
    caller forever."""


def run_ingest_pipeline(
    document_id: str,
    user_id: str,
    storage_path: str,
    *,
    client=None,
    parser=None,
    chunker=None,
    embedder=None,
) -> None:
    """download -> parse -> chunk -> embed -> upload figures -> insert
    chunks -> mark ready. Every stage dependency is injectable so tests
    exercise this exact function with fakes, not a copy of its logic.

    No-partial-chunk-data guarantee (SCOPE.md): all chunk rows for this
    document are written in exactly one bulk INSERT, which Postgres runs
    as a single atomic statement — every row lands or none do. Every
    stage that can fail (download, parse, chunk, embed, figure upload)
    happens strictly before that call, so a failure anywhere before it
    means the insert is never attempted. The best-effort delete in
    `_fail_document` below is defense-in-depth for future changes to
    this function, not the primary guarantee.

    Figure uploads landing in storage before a later stage fails are a
    known, accepted gap: SCOPE.md's no-partial-data requirement is
    scoped to the `chunks` table specifically, not storage objects.

    Dependency construction (client, Parser, Chunker, Embedder) happens
    inside the try block, not before it (Codex review, 2026-07-23): a
    construction failure — e.g. a missing env var, a model that fails to
    load — used to propagate straight out of this function, leaving the
    document stuck at 'uploaded' with no status update and no error
    message, since it happened before there was anything to catch it.
    """
    opened_images: list = []
    resolved_client = client
    try:
        if not _storage_path_belongs_to_user(storage_path, user_id):
            raise IngestInvariantError(
                f"storage_path {storage_path!r} does not belong to user {user_id!r} "
                f"(expected prefix 'uploads/{user_id}/') — refusing to process"
            )

        resolved_client = resolved_client or get_service_role_client()
        parser = parser or Parser()
        chunker = chunker or Chunker()
        embedder = embedder or Embedder()

        queries.mark_parsing(resolved_client, document_id)

        # storage_path is "uploads/{user_id}/{filename}" (API_CONTRACT.md) —
        # includes the bucket name itself, but .from_("uploads") already
        # scopes to that bucket, so the prefix must be stripped here or the
        # SDK requests "uploads/uploads/..." and 404s.
        in_bucket_path = storage_path.removeprefix("uploads/")
        file_bytes = resolved_client.storage.from_("uploads").download(in_bucket_path)
        parsed = parser.parse(file_bytes)

        # Approximation, not a Docling-native page count: the highest page
        # number seen among *extracted* elements. A trailing page with no
        # elements we model (e.g. fully blank) would undercount by one.
        page_count = max((e.page_number for e in parsed.elements), default=None)
        queries.mark_parsed(resolved_client, document_id, page_count=page_count)

        chunks = chunker.chunk(parsed)
        opened_images = [c.image for c in chunks if c.image is not None]

        vectors = embedder.embed(chunks)  # [] chunks -> [] vectors; raises EmbedError on failure
        queries.mark_embedded(resolved_client, document_id)

        figure_paths = _upload_figures(resolved_client, user_id, document_id, chunks)

        rows = queries.build_chunk_rows(
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
            vectors=vectors,
            figure_paths=figure_paths,
            elements=parsed.elements,
        )
        queries.insert_chunks(resolved_client, rows)

        queries.mark_ready(resolved_client, document_id)
    except Exception as exc:
        logger.exception("ingest pipeline failed for document %s", document_id)
        _fail_document(resolved_client, document_id, exc)
    finally:
        # FEAT-004's image ownership contract: Chunk images are the
        # caller's to close after use. This pipeline is the final caller.
        for image in opened_images:
            image.close()


def _fail_document(client, document_id: str, exc: Exception) -> None:
    """Best-effort failure handling (Codex review, 2026-07-23). If
    `client` never got constructed, there is no client to write with —
    log at ERROR and stop; the document stays at whatever status it
    already had, visible only via logs. If `client` exists but a cleanup
    call itself fails (a second, independent problem), that failure must
    not propagate — each call is attempted independently so one failing
    doesn't skip the other, and both failure paths log at ERROR with
    enough context that the document is never stuck silently."""
    if client is None:
        logger.error(
            "ingest pipeline for document %s failed before a DB client could be constructed — "
            "status was never updated, no chunk cleanup was possible: %s",
            document_id,
            exc,
        )
        return

    try:
        queries.delete_chunks_for_document(client, document_id)
    except Exception:
        logger.error(
            "ingest pipeline failure-cleanup: delete_chunks_for_document itself failed for "
            "document %s (original pipeline error: %s)",
            document_id,
            exc,
            exc_info=True,
        )

    try:
        queries.mark_failed(client, document_id, error=str(exc))
    except Exception:
        logger.error(
            "ingest pipeline failure-cleanup: mark_failed itself failed for document %s "
            "(original pipeline error: %s) — document may be stuck at an intermediate status "
            "with zero trace beyond this log line",
            document_id,
            exc,
            exc_info=True,
        )


def _upload_figures(client, user_id: str, document_id: str, chunks) -> dict[int, str]:
    figure_paths: dict[int, str] = {}
    for chunk in chunks:
        if chunk.image is None:
            continue
        path = f"{user_id}/{document_id}/{chunk.chunk_index}.png"
        buffer = BytesIO()
        chunk.image.save(buffer, format="PNG")
        client.storage.from_("figures").upload(path, buffer.getvalue(), file_options={"content-type": "image/png"})
        figure_paths[chunk.chunk_index] = path
    return figure_paths
