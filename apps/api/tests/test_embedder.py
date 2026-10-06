# Tests for [FEAT-006] Voyage embedder wrapper
#
# Most tests here use a fake in-process Voyage client (fast, deterministic,
# no network) — see FakeVoyageClient. Retry-specific tests use a *real*
# voyageai.Client (api_key="dummy", never actually sent — see below) with
# voyageai.MultimodalEmbedding.create patched at the module level, so the
# SDK's own tenacity-based retry logic (exponential backoff, exception
# filtering) is genuinely exercised rather than assumed. One test is gated
# behind RUN_REAL_VOYAGE_TEST=1 and makes a real API call — skipped by
# default so routine test runs don't burn free-tier quota.
#
# Extended 2026-07-23 per Codex review: FakeVoyageClient originally
# returned an IDENTICAL vector for every input regardless of order or
# count — a test double that could never have caught a cardinality/
# correspondence bug (5 chunks in, 4 vectors back, no error). It now
# returns a distinct, index-derived vector per input so tests can assert
# real correspondence, not just "the right number of same-looking things
# came back." A FakeTokenizer is also injected everywhere so no test
# triggers a real Hugging Face download for Voyage's real tokenizer,
# which the default (non-test) path now uses for batch-splitting token
# counts instead of chunker.py's char/4 proxy.

import copy
import os
from unittest.mock import patch

import httpx
import pytest
import voyageai
from google.genai.errors import ClientError as GeminiClientError
from PIL import Image
from voyageai.error import AuthenticationError, InvalidRequestError, RateLimitError

from services.chunker import Chunk, Chunker
from services.embedder import (
    GEMINI_DEFAULT_RETRY_DELAY_SECONDS,
    GEMINI_FALLBACK_MAX_ATTEMPTS,
    GEMINI_MAX_INPUT_TOKENS,
    GEMINI_MAX_INPUTS_PER_BATCH,
    MAX_INPUTS_PER_BATCH,
    Embedder,
    EmbedError,
    _batch_chunks,
    _chunk_to_input,
    _estimate_gemini_input_tokens,
    _gemini_image_tokens,
    _is_gemini_rate_limit_error,
    _parse_gemini_retry_delay_seconds,
    _RetryAfterAwareVoyageClient,
    _GEMINI_SAFE_INPUT_TOKENS,
)
from services.parser import ElementType, Parser

FIXTURES = "tests/fixtures"


def load(name: str) -> bytes:
    with open(f"{FIXTURES}/{name}", "rb") as f:
        return f.read()


def make_chunk(content="some text", image=None, chunk_index=0):
    return Chunk(
        chunk_index=chunk_index,
        element_type=ElementType.TEXT if image is None else ElementType.FIGURE,
        page_numbers=[1],
        source_element_indices=[0],
        content=content,
        image=image,
    )


class FakeTokenEncoding:
    def __init__(self, ids):
        self.ids = ids


class FakeTokenizer:
    """Stands in for voyageai.Client.tokenizer(MODEL) — deterministic,
    no network, no real accuracy claim (real accuracy is Voyage's own
    tokenizer's job, exercised separately, see test_batching below).
    Token count approximated as len(text)//4, same shape as
    chunker.py's proxy, purely so batching-logic tests have *some*
    predictable count to split against."""

    def encode(self, text):
        return FakeTokenEncoding(ids=list(range(len(text) // 4)))


class FakeVoyageResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class FakeVoyageClient:
    """Records every call for assertions. Returns a DISTINCT,
    index-derived vector per input (not one reused vector) so tests can
    assert real chunk<->vector correspondence, not just count equality —
    a fake returning identical vectors for every input would never be
    able to catch a misalignment/cardinality bug."""

    def __init__(self, dim=1024):
        self.dim = dim
        self.calls = []
        self._next_id = 0

    def multimodal_embed(self, inputs, model, input_type=None, output_dimension=None, **kwargs):
        self.calls.append(
            {"inputs": inputs, "model": model, "input_type": input_type, "output_dimension": output_dimension}
        )
        dim = output_dimension or self.dim
        vectors = []
        for _ in inputs:
            vectors.append([float(self._next_id)] * dim)
            self._next_id += 1
        return FakeVoyageResult(vectors)


class FakeGeminiEmbedding:
    def __init__(self, values):
        self.values = values


class FakeGeminiResponse:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class FakeGeminiModels:
    """Stands in for genai.Client().models — records every embed_content
    call for fallback-wiring assertions. Returns a distinct, index-derived
    vector per Content (same discipline as FakeVoyageClient) so tests can
    assert real correspondence, not just count equality."""

    def __init__(self, dim=1024, fail=False):
        self.dim = dim
        self.fail = fail
        self.calls = []
        self._next_id = 0

    def embed_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.fail:
            raise RuntimeError("simulated Gemini failure")
        embeddings = []
        for _ in contents:
            embeddings.append(FakeGeminiEmbedding(values=[float(self._next_id)] * self.dim))
            self._next_id += 1
        return FakeGeminiResponse(embeddings)


class FakeGeminiClient:
    def __init__(self, dim=1024, fail=False):
        self.models = FakeGeminiModels(dim=dim, fail=fail)


class ShortCountVoyageClient:
    """Always returns fewer embeddings than inputs submitted — simulates
    the exact bug Codex found: no error, just a misaligned response."""

    def __init__(self, missing=1):
        self.missing = missing

    def multimodal_embed(self, inputs, model, input_type=None, output_dimension=None, **kwargs):
        dim = output_dimension or 1024
        short_count = max(0, len(inputs) - self.missing)
        return FakeVoyageResult([[0.1] * dim for _ in range(short_count)])


def make_embedder(client=None):
    return Embedder(client=client or FakeVoyageClient(), tokenizer=FakeTokenizer())


# --- 2026-08-18 real production incident: a 108-chunk document exhausted
# Voyage's real 3 RPM ceiling, correctly fell back to Gemini, then ALSO
# exhausted Gemini's real 100 RPM ceiling on the very first fallback call
# — total ingest failure. Root cause: neither provider's retry logic read
# the real, server-provided retry delay both actually expose. See
# .agent/MEMORY.md and the module comments in services/embedder.py for
# the full investigation. The payload below is the REAL error text from
# this incident's Render logs, used verbatim, not a paraphrase.
_REAL_INCIDENT_GEMINI_429_PAYLOAD = {
    "error": {
        "code": 429,
        "message": (
            "You exceeded your current quota, please check your plan and billing details. "
            "For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. "
            "To monitor your current usage, head to: https://ai.dev/rate-limit. \n"
            "* Quota exceeded for metric: generativelanguage.googleapis.com/embed_content_free_tier_requests, "
            "limit: 100, model: gemini-embedding-2\nPlease retry in 15.65861672s."
        ),
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.Help",
                "links": [
                    {
                        "description": "Learn more about Gemini API quotas",
                        "url": "https://ai.google.dev/gemini-api/docs/rate-limits",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "quotaMetric": "generativelanguage.googleapis.com/embed_content_free_tier_requests",
                        "quotaId": "EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier",
                        "quotaDimensions": {"model": "gemini-embedding-2", "location": "global"},
                        "quotaValue": "100",
                    }
                ],
            },
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "15s"},
        ],
    }
}


def _real_gemini_429_error(retry_delay: str | None = "15s") -> GeminiClientError:
    """Builds a REAL google.genai.errors.ClientError the same way the SDK
    itself does (errors.py's raise_for_response) — a real httpx.Response,
    not a hand-rolled fake with stubbed attributes — using the exact
    verbatim payload from the 2026-08-18 incident, optionally with a
    different or absent retryDelay to test parsing/fallback specifically.
    Mirrors test_verifier.py's own `_fake_client_error` helper, which
    uses this identical real-httpx.Response technique for the same SDK."""
    payload = copy.deepcopy(_REAL_INCIDENT_GEMINI_429_PAYLOAD)
    if retry_delay is None:
        payload["error"]["details"] = [d for d in payload["error"]["details"] if not d["@type"].endswith("RetryInfo")]
    else:
        for d in payload["error"]["details"]:
            if d["@type"].endswith("RetryInfo"):
                d["retryDelay"] = retry_delay
    response = httpx.Response(429, json=payload)
    return GeminiClientError(429, response)


class _FlakyGeminiModels:
    """Raises the given error for the first `fail_times` embed_content
    calls, then succeeds — proves the new bounded retry genuinely
    retries and eventually returns a real result, not just parses the
    error and gives up anyway."""

    def __init__(self, error: Exception, fail_times: int, dim=1024):
        self.error = error
        self.fail_times = fail_times
        self.dim = dim
        self.calls = []

    def embed_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if len(self.calls) <= self.fail_times:
            raise self.error
        return FakeGeminiResponse([FakeGeminiEmbedding(values=[float(i)] * self.dim) for i in range(len(contents))])


class _FlakyGeminiClient:
    def __init__(self, error: Exception, fail_times: int, dim=1024):
        self.models = _FlakyGeminiModels(error, fail_times, dim=dim)


# Acceptance criterion: `Embedder.embed(chunks) -> list[Vector]` handles text-only and text+image chunks
def test_embed_handles_text_only_chunk():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)
    chunk = make_chunk(content="plain text chunk")

    vectors = embedder.embed([chunk])

    assert len(vectors) == 1
    assert fake.calls[0]["inputs"] == [["plain text chunk"]]


def test_embed_handles_text_and_image_chunk():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)
    image = Image.new("RGB", (10, 10))
    chunk = make_chunk(content="Figure 1: a test image", image=image)

    vectors = embedder.embed([chunk])

    assert len(vectors) == 1
    sent_input = fake.calls[0]["inputs"][0]
    assert sent_input == ["Figure 1: a test image", image]  # same object, not a copy


def test_embed_handles_image_only_chunk_no_caption():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)
    image = Image.new("RGB", (10, 10))
    chunk = make_chunk(content="", image=image)

    vectors = embedder.embed([chunk])

    assert len(vectors) == 1
    assert fake.calls[0]["inputs"][0] == [image]  # no empty-string segment


def test_chunk_to_input_raises_on_empty_chunk():
    empty_chunk = make_chunk(content="")
    with pytest.raises(EmbedError):
        _chunk_to_input(empty_chunk)


# Acceptance criterion: Returns 1024-dim vectors from `voyage-multimodal-3.5`
def test_returns_1024_dim_vectors():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)

    embedded = embedder.embed([make_chunk(content="a"), make_chunk(content="b")])

    assert len(embedded) == 2
    for ec in embedded:
        assert len(ec.vector) == 1024
        assert ec.provider == "voyage"
    assert fake.calls[0]["model"] == "voyage-multimodal-3.5"
    assert fake.calls[0]["output_dimension"] == 1024
    assert fake.calls[0]["input_type"] == "document"


def test_embed_empty_chunk_list_returns_empty():
    embedder = make_embedder()

    assert embedder.embed([]) == []


# --- Chunk <-> vector correspondence (Codex review 2026-07-23) --------------
#
# This is the test that would have caught the cardinality bug directly:
# not "did we get the right count back" but "does chunk N's vector
# actually correspond to chunk N", using a fake that returns distinguishable
# vectors instead of one reused one.


def test_each_chunk_maps_to_its_own_distinct_vector():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)
    chunks = [make_chunk(content=f"chunk number {i}", chunk_index=i) for i in range(5)]

    embedded = embedder.embed(chunks)

    assert len(embedded) == 5
    # FakeVoyageClient assigns strictly increasing id-derived vectors in
    # call order — every chunk's vector must be distinct from every other.
    first_values = [ec.vector[0] for ec in embedded]
    assert len(set(first_values)) == 5, "expected 5 distinct vectors, got duplicates/collisions"
    assert first_values == sorted(first_values), "vector order must match input chunk order"
    assert all(ec.provider == "voyage" for ec in embedded)


# Acceptance criterion: Batches API calls (verified real limit: 1,000 inputs / ~320,000 tokens per call, not the earlier unverified 128 guess)
def test_batches_respect_max_inputs_per_batch():
    chunks = [make_chunk(content="short", chunk_index=i) for i in range(MAX_INPUTS_PER_BATCH + 50)]

    batches = _batch_chunks(chunks, FakeTokenizer())

    assert len(batches) == 2
    assert len(batches[0]) == MAX_INPUTS_PER_BATCH
    assert len(batches[1]) == 50


def test_batches_respect_total_token_budget():
    # Each chunk ~4000 proxy tokens (chunker.py's own MAX_CHUNK_TOKENS
    # ceiling) -> the 300,000-token safety budget should split well before
    # the 1,000-input count limit would.
    big_content = "x" * (4000 * 4)  # ~4000 proxy tokens at char/4
    chunks = [make_chunk(content=big_content, chunk_index=i) for i in range(100)]

    batches = _batch_chunks(chunks, FakeTokenizer())

    assert len(batches) > 1
    assert all(len(b) < MAX_INPUTS_PER_BATCH for b in batches)  # token budget binds first, not input count


def test_batching_preserves_order_and_uses_multiple_calls():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)
    chunks = [make_chunk(content=f"chunk-{i}", chunk_index=i) for i in range(MAX_INPUTS_PER_BATCH + 10)]

    embedded = embedder.embed(chunks)

    assert len(embedded) == len(chunks)
    assert len(fake.calls) == 2  # confirms multiple batches actually dispatched as separate calls
    # Correspondence holds across batch boundaries too, not just within one.
    first_values = [ec.vector[0] for ec in embedded]
    assert first_values == sorted(first_values)
    assert len(set(first_values)) == len(chunks)


# --- Real Voyage tokenizer used for batch-splitting (Codex review) ---------


def test_batching_uses_the_real_voyage_tokenizer_by_default():
    # No tokenizer injected here — Embedder must resolve it from the
    # (fake) client's own .tokenizer(MODEL), proving the wiring calls
    # through rather than silently falling back to a proxy estimate.
    tokenizer_calls = []

    class FakeTokenizerProvidingClient(FakeVoyageClient):
        def tokenizer(self, model):
            tokenizer_calls.append(model)
            return FakeTokenizer()

    fake = FakeTokenizerProvidingClient()
    embedder = Embedder(client=fake)  # tokenizer intentionally NOT injected

    embedder.embed([make_chunk(content="hello")])

    assert tokenizer_calls == ["voyage-multimodal-3.5"]


@pytest.mark.network
def test_real_voyage_tokenizer_is_available_without_an_api_call():
    # Confirms the premise the whole batching redesign rests on: the real
    # tokenizer for THIS exact model string loads without needing a valid
    # API key or network call to Voyage itself (only a one-time, cached
    # Hugging Face download of the tokenizer file). If this becomes
    # unavailable in some environment, batching would need a fallback —
    # this test exists so that regression would be caught here, not
    # discovered as a mysterious failure in embed().
    client = voyageai.Client(api_key="dummy-key-never-sent")
    tokenizer = client.tokenizer("voyage-multimodal-3.5")

    ids = tokenizer.encode("Docify tokenizer availability check.").ids

    assert len(ids) > 0


# Acceptance criterion: Retries on 429 with exponential backoff (max 3 retries)
def test_retries_on_rate_limit_then_succeeds():
    call_count = 0

    def flaky_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RateLimitError("simulated 429")
        return _fake_sdk_response(1)

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=flaky_create):
        vectors = embedder.embed([make_chunk(content="hello")])

    assert len(vectors) == 1
    assert call_count == 3  # 2 failures + 1 success, all within the SDK's own retry loop


def test_retries_exhausted_raises_embed_error_when_gemini_fallback_also_fails():
    # Gemini fallback deliberately also fails here (FakeGeminiClient(fail=True))
    # so this test stays fast/deterministic (no real network to either
    # provider) while still proving the real end state: with BOTH
    # providers exhausted, embed() raises EmbedError, not a silent partial
    # result. See test_falls_back_to_gemini_when_voyage_retries_exhausted
    # below for the case where the Gemini fallback succeeds.
    call_count = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RateLimitError("simulated 429, never recovers")

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    fake_gemini = FakeGeminiClient(fail=True)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    with patch("voyageai.MultimodalEmbedding.create", side_effect=always_rate_limited):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])

    assert call_count == 3  # confirms max_retries=3 was honored, not more and not fewer
    assert len(fake_gemini.models.calls) == 1  # fallback was attempted exactly once for this batch


# Acceptance criterion: Raises `EmbedError` on non-transient failures
def test_non_transient_failure_raises_embed_error_without_retrying():
    call_count = 0

    def bad_auth(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise AuthenticationError("invalid API key")

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=bad_auth):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])

    assert call_count == 1  # AuthenticationError is not in the SDK's retry predicate — fails fast


def test_invalid_request_raises_embed_error():
    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=InvalidRequestError("bad request")):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])


# --- Gemini fallback (2026-07-31, EmbedError-on-batch fallback) ------------
#
# HIGH SCRUTINY (task brief): unlike FEAT-017's OCR tiers (interchangeable
# string output regardless of which tier produced it), a bug here could
# silently corrupt retrieval quality by mixing incomparable embedding
# spaces. These tests exist specifically to prove the fallback is
# genuinely last-resort — never triggered on a healthy Voyage call, never
# triggered on a non-retryable Voyage failure, only on a real exhausted
# transient failure — and that every fallback vector is tagged
# provider="gemini" so nothing downstream can mistake it for Voyage's
# space.


def test_falls_back_to_gemini_when_voyage_retries_exhausted():
    call_count = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RateLimitError("simulated 429, never recovers")

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    fake_gemini = FakeGeminiClient()
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    with patch("voyageai.MultimodalEmbedding.create", side_effect=always_rate_limited):
        embedded = embedder.embed([make_chunk(content="hello"), make_chunk(content="world", chunk_index=1)])

    assert call_count == 3  # Voyage's own retries genuinely ran to exhaustion first
    assert len(fake_gemini.models.calls) == 1  # exactly one fallback call for this one batch
    assert len(embedded) == 2
    assert all(ec.provider == "gemini" for ec in embedded)
    assert all(len(ec.vector) == 1024 for ec in embedded)
    call = fake_gemini.models.calls[0]
    assert call["model"] == "gemini-embedding-2"
    assert call["config"]["task_type"] == "RETRIEVAL_DOCUMENT"
    assert call["config"]["output_dimensionality"] == 1024


def test_gemini_fallback_never_triggers_on_a_healthy_voyage_batch():
    """Cost/scope guard: the fallback must be zero-cost when Voyage simply
    works. A fake Gemini client is injected specifically so that ANY call
    to it would be caught here — if this assertion ever starts failing,
    the fallback has become opportunistic, not last-resort."""
    fake_voyage = FakeVoyageClient()
    fake_gemini = FakeGeminiClient()
    embedder = Embedder(client=fake_voyage, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    embedded = embedder.embed([make_chunk(content="a"), make_chunk(content="b")])

    assert len(fake_gemini.models.calls) == 0
    assert all(ec.provider == "voyage" for ec in embedded)


def test_gemini_fallback_never_triggers_on_a_non_retryable_voyage_failure():
    """AuthenticationError/InvalidRequestError are configuration errors,
    not a capacity problem — falling back would silently mask a broken
    Voyage integration behind Gemini instead of surfacing it loudly. A
    fake Gemini client is injected so this is a real assertion, not just
    absence-of-crash: if the fallback were ever mistakenly widened to
    catch all VoyageError, this test would catch it."""
    fake_gemini = FakeGeminiClient()
    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    with patch("voyageai.MultimodalEmbedding.create", side_effect=AuthenticationError("invalid API key")):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])

    assert len(fake_gemini.models.calls) == 0


def test_gemini_fallback_cardinality_mismatch_raises_embed_error():
    class ShortGeminiModels(FakeGeminiModels):
        def embed_content(self, *, model, contents, config=None):
            resp = super().embed_content(model=model, contents=contents, config=config)
            return FakeGeminiResponse(resp.embeddings[:-1])  # one short

    class ShortGeminiClient:
        def __init__(self):
            self.models = ShortGeminiModels()

    call_count = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RateLimitError("simulated 429, never recovers")

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=ShortGeminiClient())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=always_rate_limited):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="a"), make_chunk(content="b")])


# --- 2026-08-02: Gemini fallback made provider-aware (real, live-verified
# findings — see .agent/api-docs/gemini.md and the module comment in
# services/embedder.py) --------------------------------------------------


def test_gemini_fallback_rebatches_above_gemini_s_real_100_request_cap():
    """The batch handed to the fallback is sized against VOYAGE's real
    limits (up to MAX_INPUTS_PER_BATCH=1,000) — this confirms it gets
    correctly re-batched against GEMINI's own real, empirically-confirmed
    100-request cap (GEMINI_MAX_INPUTS_PER_BATCH) before anything is sent,
    rather than forwarded as one oversized call that would have hard-
    failed against the real API (confirmed live: 100 succeeds, 101 fails
    with a clean 400 INVALID_ARGUMENT)."""
    call_count = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RateLimitError("simulated 429, never recovers")

    n_chunks = GEMINI_MAX_INPUTS_PER_BATCH + 50  # 150 — spans two Gemini sub-batches (100 + 50)
    chunks = [make_chunk(content=f"chunk {i}", chunk_index=i) for i in range(n_chunks)]

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    fake_gemini = FakeGeminiClient()
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    with patch("voyageai.MultimodalEmbedding.create", side_effect=always_rate_limited):
        embedded = embedder.embed(chunks)

    assert len(fake_gemini.models.calls) == 2, "expected exactly 2 real Gemini calls (100 + 50), not 1 oversized call"
    assert len(fake_gemini.models.calls[0]["contents"]) == GEMINI_MAX_INPUTS_PER_BATCH
    assert len(fake_gemini.models.calls[1]["contents"]) == 50
    assert len(embedded) == n_chunks
    assert all(ec.provider == "gemini" for ec in embedded)
    # Order preserved across the re-batch split — vector values are
    # index-derived (FakeGeminiModels), so a distinct, monotonically
    # assigned value per chunk proves nothing was dropped or reordered.
    assert [ec.vector[0] for ec in embedded] == [float(i) for i in range(n_chunks)]


def test_gemini_fallback_warns_when_a_chunk_risks_the_real_token_limit(caplog):
    """A chunk whose content is long enough that the conservative local
    estimate exceeds _GEMINI_SAFE_INPUT_TOKENS must produce a visible log
    warning — converting Gemini's real, confirmed SILENT truncation (no
    error, no signal at all — see the module comment) into something an
    operator can actually see, even though it isn't prevented outright."""
    call_count = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RateLimitError("simulated 429, never recovers")

    # Comfortably over the safe threshold under the conservative 2.0
    # chars/token estimate (services/embedder.py) — real chunker.py content
    # would never reach this size at MAX_CHUNK_TOKENS=4,000 in the common
    # case, but this proves the check itself actually fires when it should.
    huge_content = "real fixture-shaped text content. " * 1000
    assert _estimate_gemini_input_tokens(make_chunk(content=huge_content)) > _GEMINI_SAFE_INPUT_TOKENS

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    fake_gemini = FakeGeminiClient()
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    with patch("voyageai.MultimodalEmbedding.create", side_effect=always_rate_limited):
        with caplog.at_level("WARNING", logger="services.embedder"):
            embedder.embed([make_chunk(content=huge_content, chunk_index=0)])

    warnings = [r for r in caplog.records if "over the safe" in r.message and str(GEMINI_MAX_INPUT_TOKENS) in r.message]
    assert warnings, f"expected a visible warning for the at-risk chunk, got log records: {[r.message for r in caplog.records]}"


def test_gemini_fallback_does_not_warn_for_a_normal_sized_chunk(caplog):
    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    fake_gemini = FakeGeminiClient()
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    with patch("voyageai.MultimodalEmbedding.create", side_effect=RateLimitError("simulated 429, never recovers")):
        with caplog.at_level("WARNING", logger="services.embedder"):
            embedder.embed([make_chunk(content="a completely ordinary, short real chunk of text", chunk_index=0)])

    assert not any("over the safe" in r.message for r in caplog.records)


# Real, live-measured finding (2026-08-02): gemini-embedding-2's image
# tokenization is a FLAT 258 tokens regardless of size — confirmed across
# 13 distinct real sizes via count_tokens, from 1x1 through 8000x6000,
# every one identical. Google's own docs describe a size-dependent tiled
# formula (e.g. 960x540 -> 1,548 tokens) — real, but for a GENERATION
# model's image understanding, confirmed NOT to apply to this embedding
# endpoint; a first attempt at this formula assumed it did and was wrong
# (caught by this project's own two real fixture figures — table.docx's
# 300x200 and slides.pptx's 400x300 — both measuring a real, identical 258
# despite the tiled formula predicting different values for each; see
# .agent/api-docs/gemini.md for the full methodology and correction).
@pytest.mark.parametrize(
    "width,height",
    [
        (300, 200),  # real fixture: table.docx's figure
        (400, 300),  # real fixture: slides.pptx's figure
        (1, 1),
        (2000, 2000),
        (8000, 6000),
    ],
)
def test_gemini_image_tokens_matches_the_real_verified_flat_constant(width, height):
    image = Image.new("RGB", (width, height))
    try:
        assert _gemini_image_tokens(image) == 258
    finally:
        image.close()


# --- Part 1 (2026-08-18 incident fix): Gemini fallback retry, honoring
# the real server-provided retryDelay --------------------------------------


def test_is_gemini_rate_limit_error_true_only_for_a_genuine_429():
    assert _is_gemini_rate_limit_error(_real_gemini_429_error()) is True
    assert _is_gemini_rate_limit_error(GeminiClientError(400, httpx.Response(400, json={"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}}))) is False
    assert _is_gemini_rate_limit_error(GeminiClientError(401, httpx.Response(401, json={"error": {"message": "bad key", "status": "UNAUTHENTICATED"}}))) is False
    assert _is_gemini_rate_limit_error(RuntimeError("not even an APIError")) is False


def test_parse_gemini_retry_delay_seconds_reads_the_real_incident_value():
    assert _parse_gemini_retry_delay_seconds(_real_gemini_429_error()) == 15.0


def test_parse_gemini_retry_delay_seconds_reads_a_different_real_value():
    """Proves this genuinely PARSES the field rather than coincidentally
    returning a hardcoded 15.0 that happens to match both the real
    incident value and the module's own default."""
    assert _parse_gemini_retry_delay_seconds(_real_gemini_429_error(retry_delay="30s")) == 30.0
    assert _parse_gemini_retry_delay_seconds(_real_gemini_429_error(retry_delay="2.5s")) == 2.5


def test_parse_gemini_retry_delay_seconds_falls_back_to_default_when_missing():
    assert _parse_gemini_retry_delay_seconds(_real_gemini_429_error(retry_delay=None)) == GEMINI_DEFAULT_RETRY_DELAY_SECONDS


def test_parse_gemini_retry_delay_seconds_falls_back_to_default_when_malformed():
    assert (
        _parse_gemini_retry_delay_seconds(_real_gemini_429_error(retry_delay="not-a-duration"))
        == GEMINI_DEFAULT_RETRY_DELAY_SECONDS
    )


def test_gemini_fallback_retries_on_the_real_incident_payload_and_succeeds():
    """The core fix, proven against the VERBATIM real incident error text
    (not a paraphrase): one failure with a real retryDelay, one retry,
    one success. sleep_fn is a plain recorder, not a real time.sleep —
    this test must run in milliseconds, not 15 real seconds, so the real
    delay VALUE is asserted directly instead of timed."""
    sleeps = []
    error = _real_gemini_429_error()  # real incident payload, retryDelay: '15s'
    fake_gemini = _FlakyGeminiClient(error=error, fail_times=1)
    real_voyage = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(
        client=real_voyage, tokenizer=FakeTokenizer(), gemini_client=fake_gemini, sleep_fn=sleeps.append
    )

    with patch("voyageai.MultimodalEmbedding.create", side_effect=RateLimitError("simulated 429, never recovers")):
        embedded = embedder.embed([make_chunk(content="hello")])

    assert len(fake_gemini.models.calls) == 2, "expected exactly 2 real Gemini calls: 1 failed + 1 succeeded"
    assert sleeps == [15.0], f"expected the retry to wait exactly the real incident's retryDelay (15.0s), got {sleeps}"
    assert len(embedded) == 1
    assert embedded[0].provider == "gemini"
    assert len(embedded[0].vector) == 1024


def test_gemini_fallback_honors_a_different_real_retry_delay_value():
    sleeps = []
    error = _real_gemini_429_error(retry_delay="7.5s")
    fake_gemini = _FlakyGeminiClient(error=error, fail_times=1)
    real_voyage = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(
        client=real_voyage, tokenizer=FakeTokenizer(), gemini_client=fake_gemini, sleep_fn=sleeps.append
    )

    with patch("voyageai.MultimodalEmbedding.create", side_effect=RateLimitError("simulated 429, never recovers")):
        embedder.embed([make_chunk(content="hello")])

    assert sleeps == [7.5]


def test_gemini_fallback_stops_retrying_after_max_attempts_and_raises_embed_error():
    """Bounded, not indefinite — this is already a last-resort fallback
    path; it must not become its own quota-burning problem."""
    sleeps = []
    error = _real_gemini_429_error()
    # Never recovers — fails every single call, more than GEMINI_FALLBACK_MAX_ATTEMPTS.
    fake_gemini = _FlakyGeminiClient(error=error, fail_times=999)
    real_voyage = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(
        client=real_voyage, tokenizer=FakeTokenizer(), gemini_client=fake_gemini, sleep_fn=sleeps.append
    )

    with patch("voyageai.MultimodalEmbedding.create", side_effect=RateLimitError("simulated 429, never recovers")):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])

    assert len(fake_gemini.models.calls) == GEMINI_FALLBACK_MAX_ATTEMPTS == 3
    assert len(sleeps) == GEMINI_FALLBACK_MAX_ATTEMPTS - 1 == 2, "sleeps between attempts only, never after the final one"


def test_gemini_fallback_does_not_retry_on_a_non_rate_limit_error():
    """Cost/scope guard: a genuine misconfiguration (bad request, auth
    failure) must fail immediately, exactly like before this fix — only
    the real 429/RESOURCE_EXHAUSTED signal is retry-worthy."""
    sleeps = []
    bad_request = GeminiClientError(
        400, httpx.Response(400, json={"error": {"message": "malformed request", "status": "INVALID_ARGUMENT"}})
    )
    fake_gemini = _FlakyGeminiClient(error=bad_request, fail_times=999)
    real_voyage = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(
        client=real_voyage, tokenizer=FakeTokenizer(), gemini_client=fake_gemini, sleep_fn=sleeps.append
    )

    with patch("voyageai.MultimodalEmbedding.create", side_effect=RateLimitError("simulated 429, never recovers")):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])

    assert len(fake_gemini.models.calls) == 1, "no retry — must fail on the very first attempt"
    assert sleeps == []


# --- Part 2 (2026-08-18 incident fix): Voyage's own retry wait strategy,
# now honoring a real server-provided retry-after header instead of a
# fixed exponential-jitter schedule -----------------------------------------


def test_voyage_retry_honors_a_real_retry_after_header_and_succeeds_on_retry():
    """_RetryAfterAwareVoyageClient overrides ONLY the wait strategy —
    same real SDK request/response machinery as every other test in this
    file (voyageai.MultimodalEmbedding.create patched), same
    stop_after_attempt(3), same retryable-exception set. The only thing
    under test here is: does a real retry-after header actually get
    read and honored, instead of thrown away."""
    sleeps = []
    call_count = 0

    def flaky_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Real shape: api_requestor.py's handle_error_response passes
            # the real HTTP response headers straight through to the
            # raised exception — headers={"retry-after": "5"} here mirrors
            # exactly what a real 429 response with that header produces.
            raise RateLimitError("simulated 429 with a real retry-after header", headers={"retry-after": "5"})
        return _fake_sdk_response(1)

    real_client = _RetryAfterAwareVoyageClient(api_key="dummy-key-never-sent", max_retries=3, sleep_fn=sleeps.append)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=flaky_create):
        vectors = embedder.embed([make_chunk(content="hello")])

    assert len(vectors) == 1
    assert call_count == 2  # 1 failure + 1 success
    assert sleeps == [5.0], f"expected the real retry-after header (5s) to be honored exactly, got {sleeps}"


def test_voyage_retry_falls_back_to_exponential_jitter_when_no_retry_after_header():
    """No header present -> behavior must be UNCHANGED from the SDK's
    original fixed jittered-exponential curve (can't assert an exact
    value since it's genuinely randomized — asserts it's a real,
    reasonable positive wait instead, and that retry still happens)."""
    sleeps = []
    call_count = 0

    def flaky_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RateLimitError("simulated 429, no retry-after header at all")  # headers defaults to {}
        return _fake_sdk_response(1)

    real_client = _RetryAfterAwareVoyageClient(api_key="dummy-key-never-sent", max_retries=3, sleep_fn=sleeps.append)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=flaky_create):
        vectors = embedder.embed([make_chunk(content="hello")])

    assert len(vectors) == 1
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 16, f"expected the original wait_exponential_jitter(initial=1, max=16) range, got {sleeps[0]}"


def test_voyage_retry_after_header_ignored_when_non_numeric():
    """A malformed/non-numeric retry-after header must degrade to the
    original jittered-exponential behavior, never crash the retry path
    itself."""
    sleeps = []
    call_count = 0

    def flaky_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RateLimitError("simulated 429 with a garbage retry-after header", headers={"retry-after": "not-a-number"})
        return _fake_sdk_response(1)

    real_client = _RetryAfterAwareVoyageClient(api_key="dummy-key-never-sent", max_retries=3, sleep_fn=sleeps.append)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=flaky_create):
        vectors = embedder.embed([make_chunk(content="hello")])

    assert len(vectors) == 1
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 16


def test_embed_query_uses_voyage_by_default():
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)

    embedder.embed_query("what color is the square?")

    assert fake.calls[0]["model"] == "voyage-multimodal-3.5"
    assert fake.calls[0]["input_type"] == "query"


def test_embed_query_uses_gemini_when_provider_is_gemini():
    fake_voyage = FakeVoyageClient()
    fake_gemini = FakeGeminiClient()
    embedder = Embedder(client=fake_voyage, tokenizer=FakeTokenizer(), gemini_client=fake_gemini)

    vector = embedder.embed_query("what color is the square?", provider="gemini")

    assert len(fake_voyage.calls) == 0  # never touches Voyage when provider="gemini"
    assert len(vector) == 1024
    call = fake_gemini.models.calls[0]
    assert call["model"] == "gemini-embedding-2"
    assert call["config"]["task_type"] == "RETRIEVAL_QUERY"


def _fake_sdk_response(n):
    class Usage:
        text_tokens = 1
        image_pixels = 0
        video_pixels = 0
        total_tokens = 1

    class Data:
        embedding = [0.1] * 1024

    class Response:
        data = [Data() for _ in range(n)]
        usage = Usage()

    return Response()


# --- Cardinality check (Codex review 2026-07-23) ----------------------------
#
# The bug: 5 chunks submitted, 4 vectors returned, no error raised, caller
# silently got a misaligned list. Fixed in embedder.py; these tests prove
# the fix actually fires and never leaks a partial result.


def test_cardinality_mismatch_raises_embed_error_with_batch_context():
    chunks = [make_chunk(content=f"chunk-{i}", chunk_index=i) for i in range(5)]
    embedder = Embedder(client=ShortCountVoyageClient(missing=1), tokenizer=FakeTokenizer())

    with pytest.raises(EmbedError) as exc_info:
        embedder.embed(chunks)

    message = str(exc_info.value)
    assert "5" in message  # expected count
    assert "4" in message  # actual count
    assert all(str(c.chunk_index) in message for c in chunks)  # batch context: which chunks were involved


def test_cardinality_mismatch_returns_no_partial_vectors():
    # The bug specifically returned a partial/misaligned list silently.
    # Confirm embed() raises before returning anything at all — no
    # partial vectors leak out even though 4 of the 5 embeddings did
    # technically come back from the fake API.
    chunks = [make_chunk(content=f"chunk-{i}", chunk_index=i) for i in range(5)]
    embedder = Embedder(client=ShortCountVoyageClient(missing=1), tokenizer=FakeTokenizer())

    try:
        embedder.embed(chunks)
        assert False, "expected EmbedError"
    except EmbedError:
        pass
    # embed() raised rather than returned — there is no partial result
    # object to inspect, which is itself the assertion: a caller cannot
    # accidentally receive 4 vectors for 5 chunks and not know it.


def test_cardinality_mismatch_across_multiple_batches_still_raises():
    # A cardinality bug on the SECOND batch must still abort cleanly, not
    # get masked by the first batch's success.
    class FirstBatchOkSecondBatchShort:
        def __init__(self):
            self.call_number = 0

        def multimodal_embed(self, inputs, model, input_type=None, output_dimension=None, **kwargs):
            self.call_number += 1
            dim = output_dimension or 1024
            if self.call_number == 1:
                return FakeVoyageResult([[0.1] * dim for _ in inputs])
            return FakeVoyageResult([[0.1] * dim for _ in inputs][:-1])  # short by one

    chunks = [make_chunk(content=f"chunk-{i}", chunk_index=i) for i in range(MAX_INPUTS_PER_BATCH + 5)]
    embedder = Embedder(client=FirstBatchOkSecondBatchShort(), tokenizer=FakeTokenizer())

    with pytest.raises(EmbedError):
        embedder.embed(chunks)


# --- FEAT-004 image ownership contract --------------------------------------


def test_embedder_never_closes_the_chunk_image():
    embedder = make_embedder()
    image = Image.new("RGB", (10, 10))
    chunk = make_chunk(content="a figure", image=image)

    embedder.embed([chunk])

    # PIL.Image has no public "is closed" flag, but a closed image raises
    # on any further access — this proves the image is still fully usable
    # after embed() returns, i.e. embedder.py never closed it.
    image.load()
    assert image.size == (10, 10)


def test_embedder_does_not_call_image_close():
    embedder = make_embedder()
    image = Image.new("RGB", (10, 10))
    chunk = make_chunk(content="a figure", image=image)

    with patch.object(Image.Image, "close") as mock_close:
        embedder.embed([chunk])

    mock_close.assert_not_called()


# --- Real chunker output (not synthetic) ------------------------------------


def test_embed_real_chunks_from_table_heavy_pdf():
    # Fake Voyage client (no network), but every other part of the
    # pipeline — Docling parse, chunking, Tier-1/2 caption association —
    # is real, exercising actual text and table/figure chunk shapes
    # rather than only hand-built ones.
    doc = Parser().parse(load("table_heavy.pdf"))
    chunks = Chunker().chunk(doc)
    fake = FakeVoyageClient()
    embedder = make_embedder(fake)

    embedded = embedder.embed(chunks)

    assert len(embedded) == len(chunks)
    assert all(len(ec.vector) == 1024 for ec in embedded)
    assert all(ec.provider == "voyage" for ec in embedded)
    # Correspondence, not just count: every chunk's vector must be distinct.
    first_values = [ec.vector[0] for ec in embedded]
    assert len(set(first_values)) == len(chunks)
    # table_heavy.pdf has no figures, but does have table chunks with
    # merged caption text — confirm at least one multi-segment input was
    # NOT sent (table content stays a single text segment; images are
    # figure-only) and that plain single-segment inputs went through.
    assert all(len(call_input) == 1 for call in fake.calls for call_input in call["inputs"])


# --- Lazy client construction (2026-07-27, closes a FEAT-017-shaped gap) ---
#
# Same bug class FEAT-017's audit found and fixed for GeminiOcrClient/
# OcrSpaceClient: bare voyageai.Client() construction was confirmed live to
# raise AuthenticationError immediately if VOYAGE_API_KEY is absent, which
# meant a bare Embedder() (and by extension Retriever(), which builds a
# default Embedder()) crashed in ANY environment missing VOYAGE_API_KEY —
# even for callers that never actually call embed()/embed_query(). Fixed by
# deferring real client construction to first actual use.


def test_embedder_construction_never_touches_network_or_requires_api_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    Embedder()  # must not raise


def test_embedder_embed_fails_with_embed_error_not_a_raw_sdk_crash_when_api_key_absent(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    embedder = Embedder()  # no client injected -> real lazy client, no key present

    # Must fail as EmbedError (embed()'s own documented contract — "auth,
    # invalid request, ..." — see the class docstring), not let a raw
    # voyageai.error.AuthenticationError escape uncaught. This specifically
    # exercises _get_tokenizer()'s client resolution, which embed()'s batch
    # loop calls before its own try/except.
    with pytest.raises(EmbedError):
        embedder.embed([make_chunk(content="hello")])


def test_embedder_embed_query_fails_with_embed_error_not_a_raw_sdk_crash_when_api_key_absent(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    embedder = Embedder()

    with pytest.raises(EmbedError):
        embedder.embed_query("hello")


# --- Real API integration (opt-in only) -------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_VOYAGE_TEST") != "1",
    reason="set RUN_REAL_VOYAGE_TEST=1 to run a real Voyage API call (uses free-tier quota)",
)
def test_real_voyage_api_call_returns_1024_dim_vector():
    embedder = Embedder()  # real client, reads VOYAGE_API_KEY from env
    chunk = make_chunk(content="Docify integration test: a short real embedding call.")

    embedded = embedder.embed([chunk])

    assert len(embedded) == 1
    assert embedded[0].provider == "voyage"
    assert len(embedded[0].vector) == 1024
    assert all(isinstance(x, float) for x in embedded[0].vector)


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_GEMINI_EMBED_TEST") != "1",
    reason="set RUN_REAL_GEMINI_EMBED_TEST=1 to run a real Gemini embedding API call (uses free-tier quota)",
)
def test_real_gemini_fallback_produces_1024_dim_normalized_vector():
    """Real, opt-in confirmation of Step 0's live findings: gemini-embedding-2
    accepts multimodal Content over GEMINI_API_KEY (no Vertex auth) and
    returns an already-normalized 1024-dim vector after Matryoshka
    truncation."""
    import math

    from services.embedder import _chunk_to_gemini_content, _default_gemini_client, _gemini_embed_contents

    client = _default_gemini_client()
    chunk = make_chunk(content="Docify integration test: a short real Gemini embedding call.")
    content = _chunk_to_gemini_content(chunk)

    vectors = _gemini_embed_contents(client, [content], task_type="RETRIEVAL_DOCUMENT")

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    norm = math.sqrt(sum(v * v for v in vectors[0]))
    assert norm == pytest.approx(1.0, abs=1e-3)
