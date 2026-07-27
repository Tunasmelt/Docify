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

import os
from unittest.mock import patch

import pytest
import voyageai
from PIL import Image
from voyageai.error import AuthenticationError, InvalidRequestError, RateLimitError

from services.chunker import Chunk, Chunker
from services.embedder import MAX_INPUTS_PER_BATCH, Embedder, EmbedError, _batch_chunks, _chunk_to_input
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

    vectors = embedder.embed([make_chunk(content="a"), make_chunk(content="b")])

    assert len(vectors) == 2
    for v in vectors:
        assert len(v) == 1024
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

    vectors = embedder.embed(chunks)

    assert len(vectors) == 5
    # FakeVoyageClient assigns strictly increasing id-derived vectors in
    # call order — every chunk's vector must be distinct from every other.
    first_values = [v[0] for v in vectors]
    assert len(set(first_values)) == 5, "expected 5 distinct vectors, got duplicates/collisions"
    assert first_values == sorted(first_values), "vector order must match input chunk order"


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

    vectors = embedder.embed(chunks)

    assert len(vectors) == len(chunks)
    assert len(fake.calls) == 2  # confirms multiple batches actually dispatched as separate calls
    # Correspondence holds across batch boundaries too, not just within one.
    first_values = [v[0] for v in vectors]
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


def test_retries_exhausted_raises_embed_error():
    call_count = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RateLimitError("simulated 429, never recovers")

    real_client = voyageai.Client(api_key="dummy-key-never-sent", max_retries=3)
    embedder = Embedder(client=real_client, tokenizer=FakeTokenizer())

    with patch("voyageai.MultimodalEmbedding.create", side_effect=always_rate_limited):
        with pytest.raises(EmbedError):
            embedder.embed([make_chunk(content="hello")])

    assert call_count == 3  # confirms max_retries=3 was honored, not more and not fewer


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

    vectors = embedder.embed(chunks)

    assert len(vectors) == len(chunks)
    assert all(len(v) == 1024 for v in vectors)
    # Correspondence, not just count: every chunk's vector must be distinct.
    first_values = [v[0] for v in vectors]
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

    vectors = embedder.embed([chunk])

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    assert all(isinstance(x, float) for x in vectors[0])
