import logging
import threading
import uuid
from collections import OrderedDict
from io import BytesIO

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.documents import (
    DocumentListResponse,
    DocumentResponse,
    RenameDocumentRequest,
    SourceContextBlock,
    SourceContextResponse,
)
from services.figure_fetcher import signed_figure_url
from routes._pagination import decode_cursor, encode_cursor
from routes.ingest import STUCK_DOCUMENT_THRESHOLD_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter()

# Matches .agent/SCHEMA.md's document_status enum exactly. Validated here
# so an invalid ?status= value gets a clean 422 instead of surfacing a
# raw Postgres "invalid input value for enum document_status" error —
# PostgREST would otherwise try to cast the string straight into the
# enum column and let Postgres reject it.
_VALID_STATUSES = {"uploaded", "parsing", "embedded", "ready", "failed"}


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
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
            cursor_created_at = decode_cursor(cursor)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "invalid cursor"))

    user_id = request.state.user_id
    client = get_service_role_client()

    # Lazy stuck-document reaper (2026-08-02, FEAT-024 follow-up) — no
    # scheduler, no cron dependency, fires opportunistically exactly when
    # a user is looking at their own document list. Real failure mode
    # this closes: a background task that dies mid-flight (the pre-
    # FEAT-027 Render OOM crash — .agent/GAPS.md's FEAT-024 entry — being
    # the proven real example) leaves its document stuck in 'parsing' or
    # 'embedded' forever, with nothing to ever move it out of that state
    # on its own. Runs before the real query below so a just-reaped
    # document shows up as 'failed' (recoverable via POST /reindex) in
    # THIS same response, not one request later.
    reaped = queries.reap_stale_documents(client, user_id=user_id, threshold_seconds=STUCK_DOCUMENT_THRESHOLD_SECONDS)
    if reaped:
        logger.warning("reaped %d stale document(s) for user %s: %s", len(reaped), user_id, reaped)

    rows = queries.list_documents(client, user_id=user_id, status=status, limit=limit, cursor_created_at=cursor_created_at)

    # Fetched limit + 1 to detect whether another page exists without a
    # separate count query — the probe row itself is never returned.
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1]["created_at"]) if has_more and page else None

    return DocumentListResponse(documents=[DocumentResponse(**row) for row in page], next_cursor=next_cursor)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, request: Request):
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


# Same bound as the filenames real file systems allow.
FILENAME_MAX_LENGTH = 255


@router.patch("/documents/{document_id}", response_model=DocumentResponse)
def rename_document(document_id: str, payload: RenameDocumentRequest, request: Request):
    filename = payload.filename.strip()
    if not filename:
        return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "filename must not be empty"))
    if len(filename) > FILENAME_MAX_LENGTH:
        return JSONResponse(
            status_code=422,
            content=error_envelope("VALIDATION_ERROR", f"filename must be at most {FILENAME_MAX_LENGTH} characters"),
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        return JSONResponse(
            status_code=422, content=error_envelope("VALIDATION_ERROR", "filename must not contain control characters")
        )

    row = queries.rename_document(
        get_service_role_client(), document_id=document_id, user_id=request.state.user_id, filename=filename
    )
    if row is None:
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
def delete_document(document_id: str, request: Request):
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


# ── Page preview ────────────────────────────────────────────────────────────
# Renders one page of a PDF as a PNG, optionally with a rectangle (a citation's
# bbox, in PDF points from the top-left) highlighted — what "Open page N in
# document" shows. PDF only: DOCX/HTML have no pages, and PPTX slides aren't
# rendered server-side. No vendor API calls, so not rate-limited.
PAGE_IMAGE_RESOLUTION = 110
_HIGHLIGHT_FILL = (255, 196, 0, 70)
_HIGHLIGHT_OUTLINE = (214, 140, 0, 255)

# Rendering means downloading the whole PDF from Storage and rasterizing a
# page, so rendered pages (without highlight) are cached in memory per
# (document, page) and the highlight is drawn per request. A document's file
# never changes under the same id (reindex re-reads the same object), so
# entries can't go stale. ~32MB is roughly 100-300 pages on the 512MB instance.
PAGE_CACHE_MAX_BYTES = 32 * 1024 * 1024
PAGE_IMAGE_CACHE_CONTROL = "private, max-age=86400"


class _ByteLRU:
    """Thread-safe LRU of bytes values with a total size budget (sync routes
    run in FastAPI's threadpool)."""

    def __init__(self, max_bytes: int):
        self._max_bytes = max_bytes
        self._items: OrderedDict[object, bytes] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key) -> bytes | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key, value: bytes) -> None:
        if len(value) > self._max_bytes:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._size -= len(old)
            self._items[key] = value
            self._size += len(value)
            while self._size > self._max_bytes:
                _, evicted = self._items.popitem(last=False)
                self._size -= len(evicted)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0


_page_cache = _ByteLRU(PAGE_CACHE_MAX_BYTES)


@router.get("/documents/{document_id}/pages/{page_number}/image")
def get_page_image(
    document_id: str,
    page_number: int,
    request: Request,
    x0: float | None = None,
    y0: float | None = None,
    x1: float | None = None,
    y1: float | None = None,
):
    user_id = request.state.user_id
    client = get_service_role_client()

    row = queries.get_document_file(client, document_id=document_id, user_id=user_id)
    if row is None:
        return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "document not found"))
    if row["mime_type"] != "application/pdf":
        return JSONResponse(
            status_code=422,
            content=error_envelope("VALIDATION_ERROR", "page previews are only available for PDF documents"),
        )
    if page_number < 1:
        return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "page_number must be >= 1"))

    # Ownership was checked above; the cache is only consulted after that.
    cache_key = (document_id, page_number)
    page_png = _page_cache.get(cache_key)
    if page_png is None:
        try:
            file_bytes = client.storage.from_("uploads").download(row["storage_path"].removeprefix("uploads/"))
        except Exception:
            logger.warning("get_page_image: storage download failed for document %s", document_id)
            return JSONResponse(
                status_code=500, content=error_envelope("STORAGE_ERROR", "couldn't load this document's file")
            )

        try:
            page_png = _render_page_png(file_bytes, page_number)
        except Exception:
            logger.warning(
                "get_page_image: failed to render page %s of document %s", page_number, document_id, exc_info=True
            )
            return JSONResponse(
                status_code=422, content=error_envelope("VALIDATION_ERROR", "couldn't render this page")
            )
        if page_png is None:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "page not found"))
        _page_cache.put(cache_key, page_png)

    png = _draw_highlight(page_png, (x0, y0, x1, y1))
    return Response(content=png, media_type="image/png", headers={"Cache-Control": PAGE_IMAGE_CACHE_CONTROL})


def _render_page_png(file_bytes: bytes, page_number: int) -> bytes | None:
    # Imported here, not at module level: keeps pdfplumber out of the app's
    # import graph for every other route (see tests/test_parser_rewrite.py).
    import pdfplumber

    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        if page_number > len(pdf.pages):
            return None
        image = pdf.pages[page_number - 1].to_image(resolution=PAGE_IMAGE_RESOLUTION).original.convert("RGB")

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _draw_highlight(page_png: bytes, highlight: tuple) -> bytes:
    """The page with `highlight` (PDF points, top-left origin) drawn on it,
    or the page unchanged when there's no usable box."""
    from PIL import Image, ImageDraw

    if not all(v is not None for v in highlight):
        return page_png
    scale = PAGE_IMAGE_RESOLUTION / 72.0
    hx0, hy0, hx1, hy1 = (v * scale for v in highlight)
    if not (hx1 > hx0 and hy1 > hy0):
        return page_png

    with Image.open(BytesIO(page_png)) as page:
        image = page.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(
        (hx0 - 4, hy0 - 4, hx1 + 4, hy1 + 4), fill=_HIGHLIGHT_FILL, outline=_HIGHLIGHT_OUTLINE, width=3
    )
    out = BytesIO()
    # No `optimize`: this runs on every highlighted request, and optimize
    # roughly doubles encode time for a few percent smaller output.
    Image.alpha_composite(image, overlay).convert("RGB").save(out, format="PNG")
    return out.getvalue()


# ── Source context for non-PDF citations ───────────────────────────────────
# DOCX/PPTX/HTML have no page image, so "show in document" returns the cited
# chunk with its surroundings instead: the whole slide for PPTX, or up to
# SECTION_CONTEXT_RADIUS chunks either side within the same section for
# DOCX/HTML. Chunk text is all the API has: the original file isn't rendered.
PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SECTION_CONTEXT_RADIUS = 3
MAX_SLIDE_BLOCKS = 50


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _section_of(chunk: dict) -> str | None:
    return (chunk.get("metadata") or {}).get("section_heading")


def _without_heading_prefix(content: str, heading: str | None) -> str:
    """The chunker prefixes each chunk with its section heading; the dialog
    shows the heading once as its title instead."""
    if heading and content.startswith(heading):
        return content[len(heading) :].lstrip("\n")
    return content


@router.get("/documents/{document_id}/chunks/{chunk_id}/context", response_model=SourceContextResponse)
def get_source_context(document_id: str, chunk_id: str, request: Request):
    user_id = request.state.user_id
    client = get_service_role_client()
    not_found = JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "source not found"))

    if not (_is_uuid(document_id) and _is_uuid(chunk_id)):
        return not_found
    document = queries.get_document_file(client, document_id=document_id, user_id=user_id)
    if document is None:
        return not_found
    if document["mime_type"] == "application/pdf":
        return JSONResponse(
            status_code=422,
            content=error_envelope("VALIDATION_ERROR", "PDF sources use the page image endpoint"),
        )
    cited = queries.get_document_chunk(client, document_id=document_id, chunk_id=chunk_id, user_id=user_id)
    if cited is None:
        return not_found

    if document["mime_type"] == PPTX_MIME_TYPE:
        kind, label = "slide", f"Slide {cited['page_number']}"
        chunks = queries.list_document_chunks(
            client, document_id=document_id, user_id=user_id, page_number=cited["page_number"], limit=MAX_SLIDE_BLOCKS
        )
    else:
        kind, label = "section", _section_of(cited)
        index = cited["chunk_index"]
        window = queries.list_document_chunks(
            client,
            document_id=document_id,
            user_id=user_id,
            index_range=(index - SECTION_CONTEXT_RADIUS, index + SECTION_CONTEXT_RADIUS),
        )
        # Keep only the unbroken run of same-section chunks around the cited one.
        position = next(i for i, c in enumerate(window) if c["id"] == chunk_id)
        start = position
        while start > 0 and _section_of(window[start - 1]) == label:
            start -= 1
        end = position
        while end + 1 < len(window) and _section_of(window[end + 1]) == label:
            end += 1
        chunks = window[start : end + 1]

    blocks = []
    for chunk in chunks:
        is_cited = chunk["id"] == chunk_id
        content = chunk["content"] if kind == "slide" else _without_heading_prefix(chunk["content"], label)
        if not content.strip() and not is_cited and not chunk.get("figure_path"):
            continue  # a heading-only chunk, already shown as the title
        blocks.append(
            SourceContextBlock(
                chunk_id=chunk["id"],
                element_type=chunk["element_type"],
                content=content,
                cited=is_cited,
                figure_url=signed_figure_url(client, chunk["figure_path"]) if chunk.get("figure_path") else None,
            )
        )
    return SourceContextResponse(kind=kind, label=label, blocks=blocks)
