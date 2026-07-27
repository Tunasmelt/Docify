import logging

import voyageai
from voyageai.error import RateLimitError, ServiceUnavailableError, Timeout, VoyageError

from services.chunker import Chunk

logger = logging.getLogger(__name__)

MODEL = "voyage-multimodal-3.5"
OUTPUT_DIMENSION = 1024  # matches .agent/SCHEMA.md's chunks.embedding vector(1024)
MAX_RETRIES = 3

# Verified against Voyage's real server-side limits, not assumed — see
# .agent/api-docs/voyage.md. There is no client-side batch cap in the SDK
# for multimodal_embed(); the 128-input figure in an earlier draft of
# FEATURES.md was an unverified guess (it's a constant used by a separate,
# unrelated legacy text-only helper) and has been corrected there.
MAX_INPUTS_PER_BATCH = 1000
_REAL_MAX_TOTAL_TOKENS_PER_BATCH = 320_000
# Batching decisions use Voyage's own real local tokenizer for the text
# portion of each chunk (voyageai.Client.tokenizer(MODEL) — confirmed
# available for "voyage-multimodal-3.5" specifically, no API call needed,
# just a one-time HF download cached after). This replaces the char/4
# proxy chunker.py uses for its per-chunk MAX_CHUNK_TOKENS ceiling — that
# proxy was measured in FEAT-005 to be off by up to ~2.3x on a single
# chunk, which left only ~12.5% margin against the real 320,000-token
# batch limit. With real per-chunk text token counts, only the image
# portion (Voyage's own documented pixel/560 formula, not a rough
# estimate) carries any residual uncertainty, so the safety margin here
# can be — and is — much smaller than chunker.py's.
_SAFE_TOTAL_TOKENS_PER_BATCH = 300_000

Vector = list[float]


class EmbedError(Exception):
    """Non-transient embedding failure — auth, invalid request, a
    transient failure whose SDK-internal retries have already been
    exhausted (see below), or a response that doesn't correspond 1:1 with
    what was sent (wrong embedding count for the batch). Never raised for
    a failure the SDK would have retried successfully, and never raised
    alongside a partial/misaligned vector list — a batch that fails this
    way returns nothing, not something silently wrong."""


def _real_text_token_count(tokenizer, text: str) -> int:
    if not text:
        return 0
    return len(tokenizer.encode(text).ids)


def _estimate_input_tokens(chunk: Chunk, tokenizer) -> int:
    tokens = _real_text_token_count(tokenizer, chunk.content)
    if chunk.image is not None:
        width, height = chunk.image.size
        tokens += (width * height) // 560  # Voyage's documented image token-counting rule
    return tokens


def _chunk_to_input(chunk: Chunk) -> list:
    """One Voyage "input" is a list of text/image segments combined into a
    single embedding — a chunk's text content and its image (if any)
    become one multimodal input. Never reads chunk.image other than to
    pass it through; never closes it, per FEAT-004's ownership contract
    (ParsedDocument/Chunk images are the caller's to close after use)."""
    segments: list = []
    if chunk.content:
        segments.append(chunk.content)
    if chunk.image is not None:
        segments.append(chunk.image)
    if not segments:
        raise EmbedError("Chunk has neither text content nor an image to embed")
    return segments


def _batch_chunks(chunks: list[Chunk], tokenizer) -> list[list[Chunk]]:
    """Group chunks into batches respecting both real Voyage limits:
    <=1,000 inputs and <=~320,000 total tokens per call (see
    .agent/api-docs/voyage.md). A single chunk is never split across
    batches — chunker.py's own MAX_CHUNK_TOKENS ceiling already guarantees
    no chunk is anywhere close to the per-input 32,000-token limit."""
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0
    for chunk in chunks:
        tokens = _estimate_input_tokens(chunk, tokenizer)
        if current and (
            len(current) >= MAX_INPUTS_PER_BATCH or current_tokens + tokens > _SAFE_TOTAL_TOKENS_PER_BATCH
        ):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def _default_client() -> voyageai.Client:
    # max_retries=3: the Voyage SDK's own tenacity-based retry (exponential
    # backoff + jitter) already handles RateLimitError/
    # ServiceUnavailableError/Timeout natively — see .agent/api-docs/
    # voyage.md. No hand-rolled retry loop needed or wanted here.
    return voyageai.Client(max_retries=MAX_RETRIES)


class Embedder:
    def __init__(self, client: voyageai.Client | None = None, tokenizer=None):
        # Lazily resolved on first real use (embed()/embed_query()), not
        # here — a bare voyageai.Client() construction was confirmed live
        # (2026-07-27, FEAT-009 rerank follow-up) to raise
        # AuthenticationError immediately if VOYAGE_API_KEY is absent,
        # the same eager-construction crash shape FEAT-017's audit found
        # and fixed for GeminiOcrClient/OcrSpaceClient. That meant a bare
        # Embedder() (and by extension Retriever(), which builds a
        # default Embedder()) crashed in any environment missing
        # VOYAGE_API_KEY even for callers that never actually call
        # embed()/embed_query(). Fixed the same way: defer real client
        # construction until it's actually needed.
        self._client = client
        # Lazily resolved from self._get_client().tokenizer(MODEL) on
        # first use if not injected — real tests inject a fake tokenizer
        # explicitly so fast/mocked tests never trigger a real HF
        # download; the default path (no injection) always uses Voyage's
        # real tokenizer.
        self._tokenizer = tokenizer

    def _get_client(self) -> voyageai.Client:
        if self._client is None:
            self._client = _default_client()
        return self._client

    def _get_tokenizer(self):
        # embed()'s batch loop calls this BEFORE its own try/except (the
        # tokenizer is needed to decide batch boundaries in the first
        # place), so a missing/invalid API key surfacing here must still
        # become EmbedError, not a raw VoyageError escaping embed()'s
        # documented contract ("Non-transient embedding failure — auth,
        # invalid request, ...").
        if self._tokenizer is None:
            try:
                self._tokenizer = self._get_client().tokenizer(MODEL)
            except VoyageError as exc:
                raise EmbedError(f"Voyage tokenizer unavailable: {exc}") from exc
        return self._tokenizer

    def embed(self, chunks: list[Chunk]) -> list[Vector]:
        if not chunks:
            return []

        tokenizer = self._get_tokenizer()
        vectors: list[Vector] = []
        for batch in _batch_chunks(chunks, tokenizer):
            inputs = [_chunk_to_input(c) for c in batch]
            try:
                result = self._get_client().multimodal_embed(
                    inputs=inputs,
                    model=MODEL,
                    input_type="document",
                    output_dimension=OUTPUT_DIMENSION,
                )
            except (RateLimitError, ServiceUnavailableError, Timeout) as exc:
                # The SDK already retried these MAX_RETRIES times with
                # exponential backoff before this exception ever reached
                # us — by construction, retries are exhausted here.
                raise EmbedError(f"Voyage embedding failed after {MAX_RETRIES} attempts: {exc}") from exc
            except VoyageError as exc:
                raise EmbedError(f"Voyage embedding failed: {exc}") from exc

            if len(result.embeddings) != len(batch):
                chunk_indices = [c.chunk_index for c in batch]
                raise EmbedError(
                    f"Voyage returned {len(result.embeddings)} embeddings for a batch of "
                    f"{len(batch)} inputs (chunk_index values in this batch: {chunk_indices}) — "
                    "refusing to return a partial/misaligned result"
                )

            vectors.extend(result.embeddings)

        return vectors

    def embed_query(self, text: str) -> Vector:
        """Embeds a single natural-language query string for retrieval
        (FEAT-009) — deliberately separate from embed(), which is for
        ingestion-time Chunks and always uses input_type="document".
        Voyage's embeddings are asymmetric: query-side and document-side
        calls use different internal prefixes for better retrieval
        quality (.agent/api-docs/voyage.md), so a query must never be
        embedded with input_type="document". No batching needed — a
        query is always exactly one input, never a list[Chunk]."""
        try:
            result = self._get_client().multimodal_embed(
                inputs=[[text]],
                model=MODEL,
                input_type="query",
                output_dimension=OUTPUT_DIMENSION,
            )
        except (RateLimitError, ServiceUnavailableError, Timeout) as exc:
            raise EmbedError(f"Voyage query embedding failed after {MAX_RETRIES} attempts: {exc}") from exc
        except VoyageError as exc:
            raise EmbedError(f"Voyage query embedding failed: {exc}") from exc

        if len(result.embeddings) != 1:
            raise EmbedError(
                f"Voyage returned {len(result.embeddings)} embeddings for a single query input — "
                "refusing to return a mismatched result"
            )
        return result.embeddings[0]
