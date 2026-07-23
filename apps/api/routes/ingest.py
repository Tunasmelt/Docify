import logging
import posixpath
import re
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


# Our own upload flow only ever produces "uploads/{user_id}/{uuid}.{ext}"
# (API_CONTRACT.md) — nothing legitimate needs any character outside
# this set. See validate_storage_path()'s docstring for why this is a
# whitelist, not a blacklist of dangerous patterns.
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")


class StoragePathError(Exception):
    """Raised by validate_storage_path() on any validation failure.
    Callers decide how to surface it (403 in the route, a failed
    document status in the pipeline) but never expose *which* check
    failed to the client — see validate_storage_path()'s docstring.

    Carries two distinct messages, deliberately:
    - `reason`: a coarse, fixed category (e.g. "traversal_segment") safe
      to write to SERVER LOGS. Never includes the raw storage_path.
    - the exception's own str()/args[0] (`detail`): the full message,
      including the raw storage_path — used for `documents.error` and
      re-raised context. That's fine to store: the row it's written to
      belongs to whoever submitted the request (RLS-scoped), so at worst
      it echoes an attacker's own crafted string back to them, not to a
      third party.
    Security review (2026-07-23) flagged that logging the raw path lets
    another user's UUID, encoded traversal probes, or arbitrary
    attacker-chosen filename text end up in logs that may be hosted/
    aggregated externally — see CHANGELOG. `reason` is what
    post_ingest() and run_ingest_pipeline() log; `str(self)` is what
    goes to the DB only."""

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        super().__init__(detail)


def validate_storage_path(storage_path: str, user_id: str) -> str:
    """The single authorization boundary for storage_path ownership —
    called from both post_ingest() and run_ingest_pipeline(). Do not
    duplicate this check; both call sites must use this function so they
    can't drift (see .agent/MEMORY.md's anti-pattern entry on invariants
    enforced in only one of two places).

    SECURITY (2026-07-23): two rounds of empirical testing against the
    real local Supabase Storage stack, not assumption — each round's
    "fix" was tested against the live stack before being trusted.

    Round 1: a bare `storage_path.startswith(f"uploads/{user_id}/")` —
    this function's original implementation — passes
    "uploads/{attacker}/../{victim}/file.pdf" (a literal string prefix
    match, unaware of ".." semantics). Supabase Storage's server
    resolves the ".." before fetching the object — confirmed via both
    the storage3 SDK and raw HTTP, returning the victim's real content.

    Round 2: the first fix attempt — reject any raw ".." path segment,
    require `posixpath.normpath(storage_path) == storage_path` — was
    ALSO insufficient. A percent-encoded variant,
    "uploads/{attacker}/%2e%2e%2f{victim}/file.pdf", contains no literal
    ".." segment and is already normpath-canonical (posixpath doesn't
    decode URL encoding) — it passed both checks, yet the download still
    returned the victim's real content. Not a Supabase bug: per RFC
    3986, "%2e%2e" is *defined* as equivalent to "..", and some layer in
    the request chain (most likely the Storage server itself decoding
    the request URI's path component, standard HTTP behavior) does
    exactly that.

    Enumerating every spelling of ".." (literal, single-encoded,
    double-encoded, mixed-case hex, ...) is a losing game, so this
    function does not try. Instead: the "uploads/{user_id}/" prefix is
    checked first (user_id comes from the verified JWT, never from this
    string), then everything after it must match a narrow whitelist with
    NO path-separator character — real or encoded — possible by
    construction. Nothing legitimate is rejected (our own uploads are
    always "{uuid}.{ext}"), and no separator-smuggling technique, known
    or not yet invented, can get through, because no slash-equivalent
    character is permitted in that portion at all.

    The literal-'..'-segment and normpath-equality checks from round 2
    are kept as an extra, cheap layer — not required (the whitelist
    alone is sufficient, confirmed by re-running both live exploit
    attempts against this final version) but a regression safety net if
    the whitelist itself is ever loosened without this history being
    reread first.

    Returns storage_path unchanged on success; raises StoragePathError
    otherwise.
    """
    if ".." in storage_path.split("/"):
        raise StoragePathError(
            "traversal_segment",
            f"storage_path {storage_path!r} contains a '..' segment — refusing",
        )

    normalized = posixpath.normpath(storage_path)
    if normalized != storage_path:
        raise StoragePathError(
            "non_canonical_path",
            f"storage_path {storage_path!r} is not in canonical form (normalizes to {normalized!r}) — refusing",
        )

    expected_prefix = f"uploads/{user_id}/"
    if not storage_path.startswith(expected_prefix):
        raise StoragePathError(
            "wrong_owner_prefix",
            f"storage_path {storage_path!r} does not belong to user {user_id!r} "
            f"(expected prefix {expected_prefix!r}) — refusing",
        )

    remainder = storage_path[len(expected_prefix) :]
    if not remainder or not _SAFE_FILENAME.fullmatch(remainder):
        raise StoragePathError(
            "invalid_filename_characters",
            f"storage_path {storage_path!r} filename portion {remainder!r} contains a character "
            "outside the allowed set [A-Za-z0-9._-] — refusing (no path separator, literal or "
            "encoded, is permitted in this portion at all)",
        )

    return storage_path


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

    # storage_path validated before anything else is created — per
    # API_CONTRACT.md and this task's explicit ordering requirement. This
    # is the synchronous 403 path (no document row created at all);
    # run_ingest_pipeline() below calls the identical validate_storage_path()
    # again on its own, independent of this route being the only caller —
    # see that function's docstring. The specific validation failure is
    # logged server-side but never exposed in the 403 body — an attacker
    # probing this endpoint shouldn't get an oracle telling them exactly
    # which check their crafted path tripped. No document exists yet at
    # this point, so there's no document_id to log — only user_id (from
    # the verified JWT, not attacker-controlled) plus the coarse reason
    # (see StoragePathError's docstring, 2026-07-23 security review: the
    # raw storage_path itself must never reach server logs).
    try:
        validate_storage_path(payload.storage_path, user_id)
    except StoragePathError as exc:
        logger.warning("rejected /ingest storage_path for user %s: reason=%s", user_id, exc.reason)
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
        validate_storage_path(storage_path, user_id)

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
    except StoragePathError as exc:
        # Deliberately not logger.exception() here — that would dump a
        # traceback whose exception message still contains the raw
        # storage_path. Only the coarse reason + document/user context
        # go to logs (2026-07-23 security review); the full detail
        # still lands in documents.error via _fail_document below,
        # which is fine — that row is RLS-scoped to this same user.
        logger.warning(
            "ingest pipeline rejected storage_path for document %s (user %s): reason=%s",
            document_id,
            user_id,
            exc.reason,
        )
        _fail_document(resolved_client, document_id, exc)
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
