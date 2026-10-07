import logging
import posixpath
import re
import uuid
from io import BytesIO

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.ingest import IngestRequest, IngestResponse
from rate_limit import DailyLimitExceeded, check_daily_limit, daily_limit_exceeded_response, global_key, limiter
from services import ingest_queue
from services.chunker import Chunker
from services.document_model import (
    DocumentLimitError,
    IngestTimeoutError,
    MissingSourceFileError,
    ParseError,
    check_deadline,
)
from services.embedder import Embedder
from services.ingest_queue import JOB_TIME_LIMIT_SECONDS, IngestLease, LostIngestLease, TransientIngestError

JOB_TIME_LIMIT_MINUTES = JOB_TIME_LIMIT_SECONDS / 60
# Matches the upload UI's stated limit and the Storage bucket's file_size_limit.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

logger = logging.getLogger(__name__)

router = APIRouter()

# FEAT-024 (2026-07-28) — real vendor ceilings, not round numbers:
#
# Per-minute (Voyage): Embedder.embed() batches every chunk of a
# document into as few Voyage API calls as the 1000-input/300K-token
# batch ceiling allows (services/embedder.py) — a typical document
# ingest makes exactly ONE Voyage embed call. Voyage's real free-tier
# ceiling is 3 RPM (MEMORY.md, hit live during FEAT-009's own quality
# testing) — a SHARED budget across every user of this app, not
# per-user. 2/minute per user leaves at least 1 RPM of headroom for
# other concurrent activity sharing the same pool (other users'
# ingests, and every /query call also draws on this same 3 RPM via its
# own embed_query call) — tight by design, not generous, since the
# whole point is protecting a budget this thin.
#
# Per-day (Gemini): a scanned/low-confidence PAGE triggers FEAT-017's
# OCR fallback chain, tier 1 of which calls gemini-2.5-flash — real
# confirmed ceiling: 20 requests/DAY, per-model (MEMORY.md's 2026-07-26
# entry, hit live during this project's own OCR testing). A single
# scanned document can trigger MANY tier-1 calls (one per low-
# confidence page), not just one per ingest, so this cap bounds how
# many DOCUMENTS one user can push through the pipeline per day rather
# than precisely bounding total Gemini calls. 10/day per user leaves
# room for at least one other real user to also ingest several
# documents before the shared 20/day ceiling is at risk.
#
# Still-stated limitation, narrowed 2026-08-02 (see INGEST_GLOBAL_MINUTE_
# LIMIT below): these are per-user, per-route limits, not a single global
# counter shared across /ingest and /query together — one user hammering
# both routes simultaneously could still, in the worst case, exceed the
# shared vendor budget alone (that specific cross-ENDPOINT gap remains
# open, matching this feature's original scope of one route at a time).
# What's now closed is the narrower, more common version of the same
# problem WITHIN /ingest: N different users, each individually well
# under their own per-user limit, collectively exceeding the true
# account-level ceiling — INGEST_GLOBAL_MINUTE_LIMIT is a second,
# independent limit keyed by a fixed "global" key (rate_limit.py),
# enforced alongside (not instead of) the per-user ones below.
INGEST_MINUTE_LIMIT = "2/minute"
INGEST_DAY_LIMIT = "10/day"
INGEST_RATE_LIMIT_SCOPE = "ingest_action"

# 2026-08-02 (FEAT-024 follow-up) — real account-level Voyage ceiling
# (3 RPM, .agent/MEMORY.md), enforced as ONE counter across every user
# combined, not per-user. Deliberately equal to the real vendor ceiling
# itself (not a fraction of it, unlike the per-user 2/minute above) —
# this is the last line of defense specifically for the scenario the
# per-user limit structurally cannot catch: e.g. 2 users each making
# their own individually-fine 2/minute of requests still sum to 4/minute
# against a real 3/minute shared budget. Shared with POST /reindex under
# the same scope (INGEST_GLOBAL_RATE_LIMIT_SCOPE) — reindex draws on the
# identical real Voyage embed call, and the account-level ceiling doesn't
# care which route the call came from.
INGEST_GLOBAL_MINUTE_LIMIT = "3/minute"
INGEST_GLOBAL_RATE_LIMIT_SCOPE = "ingest_global"

# 2026-08-02 (FEAT-024 follow-up) — how long a document can sit in
# 'parsing'/'embedded' before GET /documents treats it as dead and reaps
# it to 'failed' (routes/documents.py). No dedicated ingest-pipeline
# latency benchmark exists in this project (FEAT-012's latency work is
# /query's end-to-end budget, ~4-8.3s — a different pipeline); derived
# instead from this pipeline's own real measured components: parsing
# itself is ~1-2s post-FEAT-027 (was up to 86.55s under the old Docling
# parser, .agent/SCOPE.md), and the real bottleneck is FEAT-017's OCR
# fallback chain — up to ~3 minutes worst-case PER low-confidence page
# (three sequential 60s per-tier timeouts, .agent/SCOPE.md's 2026-07-26
# update). A document with several bad pages could legitimately take
# 15-20+ real minutes. 30 minutes gives that generous headroom (a
# document would need ~10 consecutive worst-case OCR pages to
# legitimately still be running at that point — possible but not a
# realistic fixture/user document seen in this project so far) while
# still recovering a genuinely dead document within a bounded, honest
# window rather than leaving it stuck indefinitely. Documented, accepted
# tradeoff, not a proven-optimal number: a pathological real document
# could still be a false-positive reap — POST /reindex/{document_id}
# recovers it immediately if so.
STUCK_DOCUMENT_THRESHOLD_SECONDS = 30 * 60

# 2026-10-06 — ingest runs as a background task in the request-serving
# process (no queue). One document stuck in the OCR chain can take ~3
# minutes and most of Render's 512MB, so each user may have at most this
# many documents processing at once. Counted from the documents table
# (survives restarts, correct across instances); rows older than
# STUCK_DOCUMENT_THRESHOLD_SECONDS are treated as dead and don't count.
# Two concurrent requests can both pass the check (no lock) — acceptable
# for a soft resource cap.
MAX_CONCURRENT_INGESTS_PER_USER = 2


def _too_many_processing_response() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_envelope(
            "TOO_MANY_PROCESSING",
            f"You already have {MAX_CONCURRENT_INGESTS_PER_USER} documents processing. "
            "Wait for one to finish, then try again.",
        ),
    )


def _at_processing_cap(client, user_id: str) -> bool:
    processing = queries.count_processing_documents(
        client, user_id=user_id, started_within_seconds=STUCK_DOCUMENT_THRESHOLD_SECONDS
    )
    return processing >= MAX_CONCURRENT_INGESTS_PER_USER

# FEAT-020 (2026-07-27): extended from PDF-only to also accept DOCX,
# PPTX, and HTML — verified per-format against real fixtures, not assumed
# from "Docling supports it" alone (.agent/FEATURES.md's FEAT-020 entry
# has the full investigation). The exact mime types below are what real
# browsers/OS file pickers send for these extensions (standard,
# registered IANA media types — not guessed).
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "text/html",
}


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


def get_job_dispatcher():
    """FastAPI dependency returning what to call after a job is enqueued.
    In production that wakes the worker thread (main.py starts it); tests
    override it to drain the queue synchronously with the overridden
    pipeline runner, so they exercise the real enqueue/claim/finish path."""
    return lambda _runner: ingest_queue.wake_worker()


def get_pipeline_runner():
    """FastAPI dependency returning the background-pipeline callable.
    Indirection exists purely so tests can override it via
    `app.dependency_overrides` with a `functools.partial(run_ingest_pipeline,
    parser=..., chunker=..., embedder=...)` — real constructor injection
    into the real pipeline function, not a monkeypatch of it."""
    return run_ingest_pipeline


@router.post("/ingest", status_code=202, response_model=IngestResponse)
@limiter.shared_limit(INGEST_MINUTE_LIMIT, scope=INGEST_RATE_LIMIT_SCOPE)
@limiter.shared_limit(INGEST_GLOBAL_MINUTE_LIMIT, scope=INGEST_GLOBAL_RATE_LIMIT_SCOPE, key_func=global_key)
def post_ingest(
    payload: IngestRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    pipeline_runner=Depends(get_pipeline_runner),
    dispatch=Depends(get_job_dispatcher),
):
    # `response` is never touched directly below — it exists purely so
    # FastAPI injects an empty Response object for @limiter.limit(...) to
    # attach its X-RateLimit-*/Retry-After headers to. This route returns
    # a plain IngestResponse Pydantic model (via response_model=), not a
    # raw Response, so slowapi has nothing to inject headers into without
    # this parameter — confirmed live (2026-07-30): a real successful
    # /ingest call 500'd with "parameter `response` must be an instance
    # of starlette.responses.Response" until this was added. Caught by
    # actually running a real ingest against a live server with the
    # limiter enabled — every existing test ran with the limiter
    # disabled (conftest.py) or only exercised error paths that already
    # return a real Response (403/422/429), so this gap was invisible to
    # the test suite until now.
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
                "VALIDATION_ERROR", f"unsupported mime_type {payload.mime_type!r} (supported: PDF, DOCX, PPTX, HTML)"
            ),
        )

    if payload.size_bytes > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "VALIDATION_ERROR", f"file is larger than the {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit"
            ),
        )

    client = get_service_role_client()

    # Before the daily limit, so a refused request doesn't use up a daily slot.
    if _at_processing_cap(client, user_id):
        return _too_many_processing_response()

    # Postgres-backed daily limit (rate_limit.py) — NOT slowapi's
    # in-memory storage, replacing the old @limiter.limit(INGEST_DAY_LIMIT)
    # decorator. Shares one counter (route="ingest") with POST /reindex —
    # both draw on the identical real Voyage/Gemini daily budget.
    try:
        check_daily_limit(client, user_id=user_id, route="ingest", limit=int(INGEST_DAY_LIMIT.split("/")[0]))
    except DailyLimitExceeded:
        return daily_limit_exceeded_response()

    workspace_id = queries.resolve_workspace_id(
        client, user_id=user_id, workspace_id=str(payload.workspace_id) if payload.workspace_id else None
    )
    if workspace_id is None:
        return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "workspace not found"))

    document = queries.create_document(
        client,
        user_id=user_id,
        filename=payload.filename,
        storage_path=payload.storage_path,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        workspace_id=workspace_id,
    )

    # Queued, not run here: the ingest worker processes jobs one at a time
    # (services/ingest_queue.py). The document stays 'uploaded' until claimed.
    ingest_queue.enqueue(client, document_id=document["id"], user_id=user_id, storage_path=payload.storage_path)
    background_tasks.add_task(dispatch, pipeline_runner)

    return IngestResponse(document_id=document["id"], status=document["status"], created_at=document["created_at"])


@router.post("/reindex/{document_id}", status_code=202, response_model=IngestResponse)
@limiter.shared_limit(INGEST_MINUTE_LIMIT, scope=INGEST_RATE_LIMIT_SCOPE)
@limiter.shared_limit(INGEST_GLOBAL_MINUTE_LIMIT, scope=INGEST_GLOBAL_RATE_LIMIT_SCOPE, key_func=global_key)
def post_reindex(
    document_id: str,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    pipeline_runner=Depends(get_pipeline_runner),
    dispatch=Depends(get_job_dispatcher),
):
    """Re-runs the full ingest pipeline for an EXISTING document, from the
    file already sitting in Storage — no new upload. Real use cases this
    unlocks (2026-08-02, FEAT-024 follow-up):

    1. Recovering a document GET /documents just reaped to 'failed'
       (routes/documents.py's lazy stuck-document reaper) after a crashed
       or interrupted background task — this is what closes the durability
       gap `.agent/SCOPE.md`/`.agent/GAPS.md` have tracked since FEAT-024.
    2. Re-embedding a document that has one or more Gemini-fallback
       chunks (`chunks.embedding_provider = 'gemini'`) once Voyage's own
       quota has recovered. Real, honestly-stated behavior: this does NOT
       guarantee a Voyage-only result — `run_ingest_pipeline` calls the
       same `Embedder.embed()` FEAT-031 already uses, which falls back to
       Gemini again under the identical real conditions (Voyage's retries
       genuinely exhausted for a batch) if they're still true at reindex
       time. What reindex guarantees is a FRESH ATTEMPT against Voyage
       first, not a forced provider.
    3. Retrying FEAT-017's OCR fallback chain on a document that predates
       it, or that failed OCR the first time — same mechanism as (2): a
       fresh parse re-runs the current `Parser`, OCR chain included.

    Shares its rate limits with POST /ingest (same INGEST_RATE_LIMIT_SCOPE/
    INGEST_GLOBAL_RATE_LIMIT_SCOPE, same Postgres daily counter under
    route="ingest") — it triggers the identical real Voyage/Gemini calls
    /ingest does and draws on the same real vendor budgets, so it should
    be bound by the same real constraints, not a separate, looser set.
    """
    user_id = request.state.user_id
    client = get_service_role_client()

    document = queries.get_document_for_reindex(client, document_id=document_id, user_id=user_id)
    if document is None:
        # Same response whether document_id doesn't exist at all or
        # belongs to another user — get_document_for_reindex() scopes
        # user_id in the query itself (same discipline as GET/DELETE
        # /documents/{id}), so there's nothing here to accidentally leak.
        return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "document not found"))

    if document["status"] in ("parsing", "embedded") or ingest_queue.has_active_job(client, document_id):
        # Already queued or running: a second job would race the first.
        return JSONResponse(
            status_code=409,
            content=error_envelope("CONFLICT", "document is currently being processed"),
        )

    if _at_processing_cap(client, user_id):
        return _too_many_processing_response()

    try:
        check_daily_limit(client, user_id=user_id, route="ingest", limit=int(INGEST_DAY_LIMIT.split("/")[0]))
    except DailyLimitExceeded:
        return daily_limit_exceeded_response()

    # The existing chunks stay live: the job stages new ones and swaps them in
    # atomically (run_ingest_pipeline), so the document keeps answering while
    # it's reprocessed and keeps its old chunks if reprocessing fails.
    queries.mark_queued(client, document_id)
    ingest_queue.enqueue(client, document_id=document_id, user_id=user_id, storage_path=document["storage_path"])
    background_tasks.add_task(dispatch, pipeline_runner)

    return IngestResponse(document_id=document_id, status="uploaded", created_at=document["created_at"])


def run_ingest_pipeline(
    document_id: str,
    user_id: str,
    storage_path: str,
    *,
    client=None,
    parser=None,
    chunker=None,
    embedder=None,
    deadline: float | None = None,
    final_attempt: bool = True,
    lease: IngestLease | None = None,
) -> bool:
    """download -> parse -> chunk -> embed -> upload figures -> stage
    chunks -> swap them live -> mark ready. Every stage dependency is
    injectable so tests exercise this exact function with fakes, not a copy
    of its logic. Run by the ingest queue's worker (services/ingest_queue.py).

    Returns True when the document is ready, False when it was marked
    failed. A transient failure (anything but a parse/limit/timeout/path
    error) on a non-final attempt raises TransientIngestError instead, so
    the queue retries it; on the final attempt it marks the document failed.

    `deadline` (a time.monotonic() value) is the job's time limit, checked
    between stages and between OCR'd pages.

    Chunk swap (2026-10-06): new chunks are written to chunks_staging in one
    bulk INSERT and moved into chunks by swap_document_chunks() in one
    transaction. A reindexed document keeps answering from its old chunks
    until the swap, and a failure at any stage leaves them untouched (it
    used to leave the document with no chunks at all).

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
    new_figure_paths: list[str] = []
    resolved_client = client
    try:
        validate_storage_path(storage_path, user_id)

        resolved_client = resolved_client or get_service_role_client()
        if parser is None:
            # FEAT-027 (2026-08-01): imported here, not at module level —
            # Parser pulls in pdfplumber/docx/pptx/pytesseract/selectolax/
            # google.genai, real costs only an actual ingest run should
            # pay. A module-level import made every route registered
            # alongside this one (main.py wires up all routers eagerly)
            # pay it just from `import main`, defeating the decoupling
            # done in db/queries.py and services/embedder.py. Confirmed
            # via real psutil measurement (memory_measurement.py) that
            # `import main` no longer pulls services.parser into
            # sys.modules once this import is deferred here too.
            from services.parser import Parser

            parser = Parser(deadline=deadline)
        chunker = chunker or Chunker()
        embedder = embedder or Embedder()

        if lease:
            lease.mutate("progress", {"status": "parsing", "error": None})
            lease.mutate("discard")
        else:
            queries.mark_parsing(resolved_client, document_id)
            queries.delete_staged_chunks(resolved_client, document_id)  # interrupted attempt

        # storage_path is "uploads/{user_id}/{filename}" (API_CONTRACT.md) —
        # includes the bucket name itself, but .from_("uploads") already
        # scopes to that bucket, so the prefix must be stripped here or the
        # SDK requests "uploads/uploads/..." and 404s.
        in_bucket_path = storage_path.removeprefix("uploads/")
        try:
            file_bytes = resolved_client.storage.from_("uploads").download(in_bucket_path)
        except Exception as exc:
            if "not_found" in str(exc) or "Object not found" in str(exc):
                raise MissingSourceFileError("The uploaded file is missing from storage. Upload it again.") from exc
            raise  # other storage errors are worth retrying
        # The size the browser reported at /ingest isn't trusted; this is the real one.
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise DocumentLimitError(
                f"This file is {len(file_bytes) / 1024 / 1024:.0f} MB; the limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
            )
        # FEAT-020: Parser.parse() needs the real filename/extension to
        # tell Docling which format this actually is (it inspects the
        # extension, no content-sniffing — confirmed against the
        # installed SDK). storage_path's own trailing segment is the
        # validated "{uuid}.{ext}" object name (validate_storage_path's
        # whitelist above), not payload.filename's free-form display
        # name, since that's the value guaranteed to carry the real,
        # safe extension for whatever was actually uploaded.
        parsed = parser.parse(file_bytes, filename=storage_path.rsplit("/", 1)[-1])

        # Approximation, not a Docling-native page count: the highest page
        # number seen among *extracted* elements. A trailing page with no
        # elements we model (e.g. fully blank) would undercount by one.
        page_count = max((e.page_number for e in parsed.elements), default=None)
        if lease:
            lease.mutate("progress", {"page_count": page_count, "parsed_at": queries._now_iso()})
        else:
            queries.mark_parsed(resolved_client, document_id, page_count=page_count)

        chunks = chunker.chunk(parsed)
        opened_images = [c.image for c in chunks if c.image is not None]
        check_deadline(deadline, JOB_TIME_LIMIT_MINUTES)

        # [] chunks -> [] embedded_chunks; raises EmbedError only once BOTH
        # Voyage (its own exhausted retries) and, for the affected batch,
        # the Gemini fallback have failed (services/embedder.py). Each
        # EmbeddedChunk carries its own provider tag — a single document
        # can end up with a mix of "voyage" and "gemini" rows if only some
        # batches hit the fallback.
        embedded_chunks = embedder.embed(chunks)
        check_deadline(deadline, JOB_TIME_LIMIT_MINUTES)
        if lease:
            lease.mutate("progress", {"status": "embedded", "embedded_at": queries._now_iso()})
        else:
            queries.mark_embedded(resolved_client, document_id)

        # A fresh prefix per run, so the old chunks' figures stay valid until
        # the swap; the old objects are removed after it.
        figure_paths = _upload_figures(resolved_client, user_id, document_id, chunks, new_figure_paths)

        rows = queries.build_chunk_rows(
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
            embedded_chunks=embedded_chunks,
            figure_paths=figure_paths,
            elements=parsed.elements,
        )
        if lease:
            lease.mutate("stage", rows=rows)
        else:
            queries.insert_staged_chunks(resolved_client, rows)
        old_figure_paths = queries.list_figure_paths_for_document(resolved_client, document_id)
        if lease:
            lease.mutate("publish")
        else:
            queries.swap_document_chunks(resolved_client, document_id=document_id, user_id=user_id)
        # The new figures are referenced by live chunks now: a failure after
        # this point (e.g. in mark_ready) must not clean them up.
        live_figures = set(new_figure_paths)
        new_figure_paths.clear()
        if not lease:
            queries.mark_ready(resolved_client, document_id)
        # Archived cited figures still back historical source panels.
        retained_paths = set(queries.list_figure_paths_for_document(resolved_client, document_id))
        _remove_figures(resolved_client, [p for p in old_figure_paths if p not in retained_paths])
        return True
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
        _fail_document(resolved_client, document_id, exc, lease=lease)
        return False
    except (ParseError, IngestTimeoutError, MissingSourceFileError) as exc:
        # Permanent: the same file fails the same way next time. Includes
        # DocumentLimitError (too large, too many pages / OCR pages).
        logger.warning("ingest pipeline failed for document %s: %s", document_id, exc)
        _fail_document(resolved_client, document_id, exc, new_figure_paths, lease=lease)
        return False
    except Exception as exc:
        if isinstance(exc, LostIngestLease):
            _remove_figures(resolved_client, new_figure_paths)
            raise
        if not final_attempt:
            # Rate limits, network or storage errors: let the queue retry.
            _discard_attempt(resolved_client, document_id, new_figure_paths, lease=lease)
            raise TransientIngestError(f"{type(exc).__name__}: {exc}") from exc
        logger.exception("ingest pipeline failed for document %s", document_id)
        _fail_document(resolved_client, document_id, exc, new_figure_paths, lease=lease)
        return False
    finally:
        # FEAT-004's image ownership contract: Chunk images are the
        # caller's to close after use. This pipeline is the final caller.
        for image in opened_images:
            image.close()


def _remove_figures(client, paths: list[str]) -> None:
    """Best-effort: an orphaned figure costs storage bytes, never correctness."""
    if not paths:
        return
    try:
        client.storage.from_("figures").remove(paths)
    except Exception:
        logger.warning("ingest: couldn't remove %d figure object(s)", len(paths), exc_info=True)


def _discard_attempt(client, document_id: str, new_figure_paths: list[str], lease: IngestLease | None = None) -> None:
    """Removes what a failed attempt staged; the live chunks are untouched."""
    if client is None:
        return
    try:
        if lease:
            lease.mutate("discard")
        else:
            queries.delete_staged_chunks(client, document_id)
    except Exception:
        logger.warning("ingest: couldn't clear staged chunks for document %s", document_id, exc_info=True)
    _remove_figures(client, new_figure_paths)


def _fail_document(client, document_id: str, exc: Exception, new_figure_paths: list[str] | None = None, lease: IngestLease | None = None) -> None:
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

    # Only this attempt's staged chunks: a reindexed document's live chunks
    # stay searchable after a failure (2026-10-06).
    try:
        if lease:
            lease.mutate("discard")
        else:
            queries.delete_staged_chunks(client, document_id)
    except Exception:
        logger.error(
            "ingest pipeline failure-cleanup: delete_staged_chunks itself failed for "
            "document %s (original pipeline error: %s)",
            document_id,
            exc,
            exc_info=True,
        )
    _remove_figures(client, new_figure_paths or [])

    try:
        if lease:
            lease.mutate("progress", {"status": "failed", "error": str(exc)})
        else:
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


def _upload_figures(client, user_id: str, document_id: str, chunks, uploaded: list[str]) -> dict[int, str]:
    """Uploads under a fresh per-run prefix and appends each path to
    `uploaded` as it lands, so a failure part-way can clean up."""
    run_id = uuid.uuid4().hex[:12]
    figure_paths: dict[int, str] = {}
    for chunk in chunks:
        if chunk.image is None:
            continue
        path = f"{user_id}/{document_id}/{run_id}/{chunk.chunk_index}.png"
        buffer = BytesIO()
        chunk.image.save(buffer, format="PNG")
        client.storage.from_("figures").upload(path, buffer.getvalue(), file_options={"content-type": "image/png"})
        uploaded.append(path)
        figure_paths[chunk.chunk_index] = path
    return figure_paths
