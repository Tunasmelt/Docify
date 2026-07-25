import logging

from services.generator import GeneratorChunk
from services.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# 10 minutes — long enough that a user reading a response and clicking
# through a citation's figure doesn't race an expiring URL mid-read; short
# enough that a leaked/logged URL isn't a long-lived credential. No prior
# convention existed to match (first signed-URL use in this codebase —
# .agent/api-docs/supabase-storage-py.md).
FIGURE_URL_EXPIRY_SECONDS = 600


def signed_figure_url(client, figure_path: str) -> str | None:
    """Shared by routes/query.py (live citations) and
    routes/conversations.py (historical citations) — one place for
    building a citation's figure URL, same reuse-not-duplicate reasoning
    as fetch_generator_chunks() below being the one place that resolves
    chunk -> figure_path at all. Best-effort: a Storage hiccup here must
    not fail the whole response (same discipline as fetch_generator_chunks'
    own per-figure download try/except — a failure degrades to no
    figure_url, not a 500)."""
    try:
        result = client.storage.from_("figures").create_signed_url(figure_path, FIGURE_URL_EXPIRY_SECONDS)
        return result.get("signedURL")
    except Exception:
        logger.warning(
            "figure_fetcher: failed to create signed URL for figure_path=%s — citation will omit figure_url",
            figure_path,
        )
        return None


def fetch_generator_chunks(client, retrieved_chunks: list[RetrievedChunk]) -> list[GeneratorChunk]:
    """Adapts Retriever's output into Generator's input, fetching each
    figure chunk's image from Storage along the way. Confirmed by the
    2026-07-24 full-flow audit (item 3) that nothing upstream provides
    this: RetrievedChunk carries no image data (the RPC functions it
    calls never SELECT figure_path — FEAT-009), and FEAT-008 only
    exposes a whole-document figure_path listing (used for cascade-
    delete cleanup), not a per-chunk-id lookup. This is the first
    production code to do either.

    Order of `retrieved_chunks` is preserved — callers (this route) rely
    on position `chunks[N-1]` matching `GenerateResult.cited_indices`,
    the same 1-indexed-position contract `Generator`/`Verifier` already
    use, so this must never reorder, dedupe, or drop entries — even when
    a single figure's download fails (see below).

    A 2026-07-24 self-audit found a Storage download failure for ANY one
    figure — a genuinely missing object, a network blip — crashed the
    entire request uncaught, including citations that had nothing to do
    with the failed figure (confirmed live against a real 404 from the
    real Storage backend). Each download is now caught individually: a
    failure degrades that one chunk to a text-only entry (element_type
    changed from "figure" so it never claims to have an image it
    doesn't) rather than failing the whole batch, and is logged clearly
    — chunk_id/figure_path only, never the raw exception text, matching
    this project's established coarse-reason-over-raw-detail Storage
    error discipline (routes/documents.py's _storage_deletion_failed()).
    Degrading `element_type` rather than passing image=None through
    unchanged is deliberate: GeneratorChunk.__post_init__ still requires
    an image for anything claiming to be "figure" (that guard exists to
    catch a caller that FORGOT to fetch, not a fetch that genuinely
    failed at runtime — these are different failure modes, and this
    keeps that guard's original meaning intact rather than working
    around it silently).
    """
    if not retrieved_chunks:
        return []

    chunk_ids = [c.chunk_id for c in retrieved_chunks]
    rows = client.table("chunks").select("id,figure_path").in_("id", chunk_ids).execute().data
    figure_paths = {row["id"]: row["figure_path"] for row in rows}

    generator_chunks = []
    for chunk in retrieved_chunks:
        path = figure_paths.get(chunk.chunk_id)
        element_type = chunk.element_type
        image = None
        if path:
            try:
                image = client.storage.from_("figures").download(path)
            except Exception:
                logger.warning(
                    "figure_fetcher: failed to download figure image for chunk %s (figure_path=%s) — "
                    "degrading to a text-only entry for this citation rather than failing the whole request",
                    chunk.chunk_id,
                    path,
                )
                element_type = "text"
        generator_chunks.append(
            GeneratorChunk(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                element_type=element_type,
                page_number=chunk.page,
                document_name=chunk.document_name,
                image=image,
                # Only carried through when the download above actually
                # succeeded and element_type is still "figure" — a chunk
                # degraded to "text" (failed download) must not claim a
                # figure_path a citation-URL builder would then treat as
                # viewable, since the image fetch for it just failed.
                figure_path=path if element_type == "figure" else None,
            )
        )
    return generator_chunks
