from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

import voyageai
from google import genai
from google.genai import types
from voyageai.error import RateLimitError, ServiceUnavailableError, Timeout, VoyageError

# 2026-08-01 (FEAT-027): same fix as db/queries.py — Chunk is used only as a
# type hint here (embed()'s functions duck-type chunk.content/chunk.image,
# never need the class itself at runtime). A plain import pulled
# services.chunker -> services.parser -> (formerly) docling into every route
# that imports services.retriever -> services.embedder, including /query,
# which never touches parsing at all.
if TYPE_CHECKING:
    from services.chunker import Chunk

logger = logging.getLogger(__name__)

MODEL = "voyage-multimodal-3.5"
OUTPUT_DIMENSION = 1024  # matches .agent/SCHEMA.md's chunks.embedding vector(1024)
MAX_RETRIES = 3

# Fallback provider (2026-07-31) — see .agent/MEMORY.md's "embedding
# spaces are not comparable" entry before touching this. gemini-embedding-2
# specifically, NOT gemini-embedding-001: live verification
# (.agent/api-docs/gemini.md) confirmed -001 is genuinely text-only (a real
# image Part sent to it comes back "The text content is empty"), while
# gemini-embedding-2 accepts real multimodal (text+image) Content and,
# unlike -001, returns an already-unit-normalized vector after Matryoshka
# truncation to output_dimensionality=1024 (verified live: norm ~1.0 vs
# -001's ~0.61) — not that it matters for pgvector's own `<=>` cosine
# operator (magnitude-invariant by construction), but it removes any
# dependency on that invariance holding everywhere this vector is ever
# used. Reached over the exact same GEMINI_API_KEY / genai.Client(api_key=)
# path parser.py's OCR tier already uses — confirmed live, NOT Vertex
# AI/service-account auth, despite that being Google's historical pattern
# for multimodal embeddings.
GEMINI_MODEL = "gemini-embedding-2"
GEMINI_OUTPUT_DIMENSION = 1024
# Same reasoning as parser.py's OCR_TIMEOUT_MS: no explicit timeout means
# the genai SDK passes timeout=None straight through to httpx, which
# httpx treats as "no timeout at all," not "use a default."
GEMINI_TIMEOUT_MS = 60_000

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
# Matches the chunks.embedding_provider Postgres enum (migrations/
# 20260731_001_embedding_provider_fallback.sql) literally — these two
# string literals are the only valid values on either side.
Provider = str  # "voyage" | "gemini"


@dataclass
class EmbeddedChunk:
    """A Chunk's real vector plus which provider actually produced it.
    embed() used to return a bare list[Vector]; that contract broke the
    instant a Gemini fallback could tag SOME of a document's chunks
    differently from the rest (a mid-batch Voyage RPM exhaustion, not a
    clean whole-document failure — see .agent/GAPS.md). Every caller that
    persists a vector (db/queries.py's build_chunk_rows) needs the
    provider alongside it so retrieval can later partition by embedding
    space; nothing downstream may ever compare a `vector` across two
    EmbeddedChunks with different `provider` values via cosine
    similarity — Voyage's and Gemini's embedding spaces are NOT the same
    space, even at matching dimensionality (.agent/MEMORY.md)."""

    vector: Vector
    provider: Provider


class EmbedError(Exception):
    """Non-transient embedding failure — auth, invalid request, or a
    transient failure whose retries are exhausted on BOTH Voyage (SDK-
    internal, see below) and the Gemini fallback (this module's own
    single attempt — see _embed_batch_with_gemini_fallback), or a
    response that doesn't correspond 1:1 with what was sent (wrong
    embedding count for the batch, from either provider). Never raised
    for a failure a retry (Voyage's own, or the Gemini fallback) would
    have recovered from, and never raised alongside a partial/misaligned
    vector list — a batch that fails this way returns nothing, not
    something silently wrong."""


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


# ── Gemini fallback (2026-07-31) ─────────────────────────────────────────
#
# Deliberately last-resort: reached ONLY from embed()'s except block below,
# after Voyage's own SDK-internal retries (MAX_RETRIES=3, exponential
# backoff) are already exhausted for a given batch — never called
# speculatively, never a first choice, never opportunistic. See
# tests/test_embedder.py's cost/scope guard tests for live proof this
# never fires on a healthy Voyage call.
#
# 2026-08-02 — real, EMPIRICALLY VERIFIED Gemini limits (never checked
# when this fallback was originally built; the batch handed to this path
# was sized ONLY against Voyage's real limits — MAX_INPUTS_PER_BATCH=1000,
# _SAFE_TOTAL_TOKENS_PER_BATCH=300,000 — and sent to Gemini as-is,
# unvalidated against Gemini's own, different, real limits):
#
# - Per-input token limit: 8,192 (Google's own docs; confirmed live via
#   `client.models.count_tokens` AND by direct observation of the failure
#   mode) — 4x TIGHTER than Voyage's real 32,000. Critically, exceeding it
#   does NOT raise an error: Gemini SILENTLY TRUNCATES. Proven live, not
#   just per docs: a real 32,000-char chunk (real fixture text, repeated —
#   this project's own fixtures don't contain 32,000 distinct real chars)
#   measured at 13,195 real Gemini tokens (60% over the limit) embedded
#   successfully with no error; cosine(full input, an independently-
#   embedded ~8,192-token-equivalent prefix of the SAME text) = 0.999999
#   — for contrast, cosine(full input, genuinely unrelated real text) =
#   0.777. A near-1.0 match against the truncated-equivalent prefix, that
#   far above the "different content" baseline, is direct proof the extra
#   ~5,000 tokens were silently dropped server-side, not that the model
#   "handled" them some other way. A single chunk at chunker.py's own
#   MAX_CHUNK_TOKENS=4,000 (proxy) ceiling measured at 6,365 REAL Gemini
#   tokens in this same test (77.7% of the real 8,192 limit) — comfortably
#   under it for THIS content, but with materially less margin than the
#   ~29% worst-case utilization already verified against Voyage's real
#   32,000 limit (.agent/FEATURES.md's FEAT-006 entry) — MAX_CHUNK_TOKENS
#   was never re-validated against Gemini's tighter ceiling, and different
#   (denser) real content could plausibly exceed it. Not raised to a hard
#   block here (chunker.py's splitting-by-sentence/row logic isn't
#   duplicated in this module, and MAX_CHUNK_TOKENS itself doesn't need to
#   shrink for Voyage's own real, much larger limit) — instead, a
#   conservative local estimate flags and LOGS any at-risk chunk before it
#   silently loses content with zero signal.
# - Per-batch REQUEST COUNT: exactly 100 (Google's own docs: "at most 100
#   requests can be in one batch") — CONFIRMED live at the exact boundary:
#   100 real inputs in one batchEmbedContents call succeeded, 101 failed
#   with a clean `400 INVALID_ARGUMENT`. This is a real, CONFIRMED bug in
#   the pre-2026-08-02 fallback, not a theoretical one: a Voyage batch
#   above 100 chunks (a real, reachable case — Voyage batches up to 1,000)
#   that fell back to Gemini would have hard-failed the ENTIRE fallback
#   outright, even though Gemini could handle the same chunks fine once
#   correctly re-batched. No separate lower aggregate-token cap was found
#   up to ~119,000 real tokens across 50 real inputs in the same live
#   test — the 100-request cap is the actual binding constraint for
#   typical chunk sizes, not a total-token budget.
# - Image tokenization: a COMPLETELY DIFFERENT scheme from Voyage's
#   documented pixels/560 formula — confirmed NOT to transfer, empirically.
#   `gemini-embedding-2` costs a FLAT, CONSTANT 258 tokens per image,
#   REGARDLESS of size — confirmed live across 1x1 through 8000x6000 (13
#   distinct sizes via `count_tokens`, every single one returned exactly
#   258). Google's own docs describe a size-dependent tiled scheme
#   (crop-unit tiling, up to ~1,548 tokens for a 960x540 image) — that
#   formula is real, but for a GENERATION model's image understanding, not
#   this embedding endpoint; naively applying it here would have been
#   just as wrong as assuming Voyage's formula transfers. Two real fixture
#   figures (300x200, 400x300) that Voyage's pixels/560 formula predicts
#   at 107 and 214 tokens respectively both measured the same real 258.
#
# Full findings, real numbers, and methodology: .agent/api-docs/gemini.md.
#
# FIX: this fallback path is now provider-aware, sized against GEMINI's
# own real limits (below), not inherited from whatever Voyage-sized batch
# it happens to receive.

GEMINI_MAX_INPUTS_PER_BATCH = 100
GEMINI_MAX_INPUT_TOKENS = 8_192

# Local, no-extra-API-call estimate for the pre-send safety check below —
# deliberately conservative (i.e. deliberately overestimates token count),
# calibrated against the two real live measurements above: 16,000 real
# chars -> 6,365 real tokens (~2.51 chars/token), 32,000 chars -> 13,195
# real tokens (~2.43 chars/token). 2.0 is picked BELOW both measured
# ratios on purpose, so this estimate should never UNDERCOUNT relative to
# content resembling what was actually measured — it is a calibrated
# proxy, not a promise, since Gemini's real tokenizer was never called
# per-chunk here (that would add a real API round trip to every fallback
# embed, for a check whose only job is emitting a log warning).
_GEMINI_CHARS_PER_TOKEN_CONSERVATIVE = 2.0
# Safety margin below the real 8,192 ceiling, absorbing estimate error.
_GEMINI_SAFE_INPUT_TOKENS = 7_500


GEMINI_IMAGE_TOKENS = 258  # flat, confirmed live regardless of size — see module comment


def _gemini_image_tokens(image) -> int:
    """Gemini's REAL, empirically-verified image tokenization for
    `gemini-embedding-2` specifically (see the module-level comment
    above): a flat constant, NOT Voyage's pixels/560 formula, and NOT the
    size-dependent tiled formula Google's own docs describe for
    generation-model image understanding — confirmed live that formula
    does not apply to this embedding endpoint (tested 1x1 through
    8000x6000: every size returned the identical 258)."""
    return GEMINI_IMAGE_TOKENS


def _estimate_gemini_input_tokens(chunk: Chunk) -> int:
    tokens = int(len(chunk.content) / _GEMINI_CHARS_PER_TOKEN_CONSERVATIVE) if chunk.content else 0
    if chunk.image is not None:
        tokens += _gemini_image_tokens(chunk.image)
    return tokens


def _batch_for_gemini(chunks: list[Chunk]) -> list[list[Chunk]]:
    """Re-batches an already-Voyage-sized batch (up to MAX_INPUTS_PER_BATCH
    = 1,000 chunks, sized against Voyage's real limits by _batch_chunks
    above) into Gemini-legal sub-batches of <=GEMINI_MAX_INPUTS_PER_BATCH
    (100, empirically confirmed hard cap — see module comment) before any
    of it reaches Gemini's batchEmbedContents endpoint. The batch this
    receives was never sized with Gemini in mind; this is what makes it
    safe to actually send there."""
    return [
        chunks[i : i + GEMINI_MAX_INPUTS_PER_BATCH] for i in range(0, len(chunks), GEMINI_MAX_INPUTS_PER_BATCH)
    ]


def _default_gemini_client() -> genai.Client:
    return genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


def _image_to_png_bytes(image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _chunk_to_gemini_content(chunk: Chunk) -> types.Content:
    """Same segment logic as _chunk_to_input (text and/or image, never
    neither — _chunk_to_input already raised EmbedError before this is
    ever reached for a genuinely empty chunk), reshaped into a single
    multimodal Content for Gemini's embed_content — confirmed live
    (.agent/api-docs/gemini.md) that gemini-embedding-2 accepts a Content
    with both a text Part and an image Part and embeds them as one input,
    the same "one input per chunk" contract Voyage's multimodal_embed
    has."""
    parts: list[types.Part] = []
    if chunk.content:
        parts.append(types.Part.from_text(text=chunk.content))
    if chunk.image is not None:
        parts.append(types.Part.from_bytes(data=_image_to_png_bytes(chunk.image), mime_type="image/png"))
    return types.Content(parts=parts)


def _gemini_embed_contents(client: genai.Client, contents: list, task_type: str) -> list[Vector]:
    """Thin wrapper around one real embed_content call — batches natively
    (confirmed live: multiple `contents` entries in one call return one
    embedding per entry, in order, via the same batchEmbedContents
    endpoint models.py routes through). Raises EmbedError on any SDK
    failure or a response whose embedding count doesn't match the input
    count — same no-partial-result discipline as Voyage's path below."""
    try:
        response = client.models.embed_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={"task_type": task_type, "output_dimensionality": GEMINI_OUTPUT_DIMENSION},
        )
    except Exception as exc:  # genai raises its own exception hierarchy, not VoyageError's
        raise EmbedError(f"Gemini embedding failed: {exc}") from exc

    if len(response.embeddings) != len(contents):
        raise EmbedError(
            f"Gemini returned {len(response.embeddings)} embeddings for {len(contents)} inputs — "
            "refusing to return a partial/misaligned result"
        )
    return [list(e.values) for e in response.embeddings]


def _embed_batch_with_gemini_fallback(batch: list[Chunk], client: genai.Client | None) -> list[EmbeddedChunk]:
    """The actual fallback: re-embeds one already-failed Voyage batch via
    Gemini, tagging every resulting vector provider="gemini". A failure
    here (missing GEMINI_API_KEY, Gemini's own quota, network) raises
    EmbedError same as Voyage's own exhausted-retries path — this is
    genuinely last-resort, not a second safety net with its own further
    fallback.

    Provider-aware as of 2026-08-02 (see the module comment above for the
    real, live-verified findings this responds to): re-batches against
    Gemini's own real 100-request cap (_batch_for_gemini) before sending
    anything — the incoming `batch` was sized against Voyage's limits and
    would otherwise silently violate Gemini's tighter one — and logs a
    visible warning for any chunk estimated at real risk of Gemini's
    silent per-input truncation, so that failure mode is no longer a
    total black box even though it isn't (and can't cheaply be) prevented
    outright here."""
    try:
        resolved_client = client or _default_gemini_client()
    except KeyError as exc:
        raise EmbedError(f"Gemini fallback unavailable: {exc}") from exc

    for chunk in batch:
        estimated = _estimate_gemini_input_tokens(chunk)
        if estimated > _GEMINI_SAFE_INPUT_TOKENS:
            logger.warning(
                "chunk_index=%s estimated at ~%d Gemini tokens, over the safe %d-token threshold "
                "(real hard limit %d) — Gemini silently truncates over-limit input with no error "
                "(confirmed live, .agent/api-docs/gemini.md), so this chunk's embedding may reflect "
                "only part of its real content",
                chunk.chunk_index,
                estimated,
                _GEMINI_SAFE_INPUT_TOKENS,
                GEMINI_MAX_INPUT_TOKENS,
            )

    embedded: list[EmbeddedChunk] = []
    for sub_batch in _batch_for_gemini(batch):
        contents = [_chunk_to_gemini_content(c) for c in sub_batch]
        vectors = _gemini_embed_contents(resolved_client, contents, task_type="RETRIEVAL_DOCUMENT")
        embedded.extend(EmbeddedChunk(vector=v, provider="gemini") for v in vectors)
    return embedded


class Embedder:
    def __init__(
        self,
        client: voyageai.Client | None = None,
        tokenizer=None,
        gemini_client: genai.Client | None = None,
    ):
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
        # Same lazy-construction discipline for the Gemini fallback client
        # — a bare Embedder() must not require GEMINI_API_KEY either, since
        # the fallback is only ever reached from inside embed()'s own
        # except block, on a real exhausted Voyage failure.
        self._gemini_client = gemini_client
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

    def _get_gemini_client(self) -> genai.Client:
        if self._gemini_client is None:
            self._gemini_client = _default_gemini_client()
        return self._gemini_client

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

    def embed(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        if not chunks:
            return []

        tokenizer = self._get_tokenizer()
        embedded: list[EmbeddedChunk] = []
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
                # us — by construction, retries are exhausted here. This
                # is the ONLY tier that falls back to Gemini: the real
                # motivating scenario (.agent/GAPS.md) is a mid-batch
                # Voyage RPM exhaustion, a transient capacity problem a
                # second provider can genuinely route around. Deliberately
                # narrower than "any VoyageError" — see the plain
                # VoyageError branch below for why AuthenticationError/
                # InvalidRequestError must NOT fall back.
                logger.warning(
                    "Voyage embedding failed for a batch of %d chunk(s) after %d attempts (%s: %s) — "
                    "falling back to Gemini for this batch only",
                    len(batch),
                    MAX_RETRIES,
                    type(exc).__name__,
                    exc,
                )
                try:
                    embedded.extend(_embed_batch_with_gemini_fallback(batch, self._gemini_client))
                    continue
                except EmbedError as gemini_exc:
                    raise EmbedError(
                        f"Voyage embedding failed after {MAX_RETRIES} attempts ({exc}), and the "
                        f"Gemini fallback also failed ({gemini_exc}) — both providers exhausted "
                        f"for this batch of {len(batch)} chunk(s)"
                    ) from gemini_exc
            except VoyageError as exc:
                # A genuine misconfiguration (bad/missing VOYAGE_API_KEY,
                # a malformed request) — not a capacity problem, so NOT a
                # candidate for the Gemini fallback. Falling back here
                # would silently mask a broken Voyage integration behind
                # Gemini forever, instead of surfacing it loudly the way
                # a config error should. Matches the pre-fallback
                # behavior exactly: fails immediately, no retry, no
                # fallback attempt of any kind.
                raise EmbedError(f"Voyage embedding failed: {exc}") from exc

            if len(result.embeddings) != len(batch):
                chunk_indices = [c.chunk_index for c in batch]
                raise EmbedError(
                    f"Voyage returned {len(result.embeddings)} embeddings for a batch of "
                    f"{len(batch)} inputs (chunk_index values in this batch: {chunk_indices}) — "
                    "refusing to return a partial/misaligned result"
                )

            embedded.extend(EmbeddedChunk(vector=v, provider="voyage") for v in result.embeddings)

        return embedded

    def embed_query(self, text: str, *, provider: Provider = "voyage") -> Vector:
        """Embeds a single natural-language query string for retrieval
        (FEAT-009) — deliberately separate from embed(), which is for
        ingestion-time Chunks and always uses input_type="document"/
        task_type="RETRIEVAL_DOCUMENT". Both providers' embeddings are
        asymmetric: query-side and document-side calls use different
        internal prefixes for better retrieval quality (.agent/api-docs/
        voyage.md, .agent/api-docs/gemini.md), so a query must never be
        embedded with the document-side task type. No batching needed —
        a query is always exactly one input, never a list[Chunk].

        provider selects WHICH embedding space to query into — retriever.py
        calls this once per distinct provider actually present in the
        requested document_ids scope (never speculatively for a provider
        with no chunks in scope), since a query embedded in Voyage's space
        is meaningless compared against a Gemini-space chunk vector and
        vice versa (.agent/MEMORY.md)."""
        if provider == "gemini":
            # Same real 8,192-token limit and silent-truncation risk as
            # the document-side path above applies here too — a query is
            # realistically far too short to ever approach it, but the
            # check is cheap enough to apply uniformly rather than assume.
            estimated = int(len(text) / _GEMINI_CHARS_PER_TOKEN_CONSERVATIVE)
            if estimated > _GEMINI_SAFE_INPUT_TOKENS:
                logger.warning(
                    "query text estimated at ~%d Gemini tokens, over the safe %d-token threshold "
                    "(real hard limit %d) — Gemini silently truncates over-limit input with no error",
                    estimated,
                    _GEMINI_SAFE_INPUT_TOKENS,
                    GEMINI_MAX_INPUT_TOKENS,
                )
            vectors = _gemini_embed_contents(
                self._get_gemini_client(),
                [types.Content(parts=[types.Part.from_text(text=text)])],
                task_type="RETRIEVAL_QUERY",
            )
            return vectors[0]

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
