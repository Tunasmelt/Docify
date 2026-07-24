# Tests for [FEAT-011] Citation verifier
#
# Two tiers, same split as test_generator.py/test_retriever.py:
#
# 1. FAST/MOCKED — a fake genai client records exactly what Verifier sent
#    it and returns a caller-controlled response, so verdict/quote
#    handling, batching, and — the most important part of this feature —
#    fail-safe behavior on a broken Gemini call can all be tested
#    precisely, cheaply, and deterministically.
#
# 2. REAL — gated behind env vars, same pattern as
#    RUN_REAL_GEMINI_TEST/RUN_GENERATION_QUALITY_TEST. Includes a real
#    adversarial fixture built from table_heavy.pdf's actual content
#    (not synthetic strings) and a real end-to-end check that a claim
#    from one of FEAT-010's real generated answers verifies correctly —
#    the first real test of whether "positionally correct" and
#    "factually correct" citations are actually the same thing in
#    practice, or merely assumed to be.

import os
import time

import httpx
import pydantic
import pytest
from google.genai.errors import ClientError

from services.generator import GeneratorChunk
from services.verifier import (
    MODEL,
    Verdict,
    VerdictLabel,
    VerificationError,
    Verifier,
    _VerdictResponse,
)


# --- Fakes --------------------------------------------------------------


class FakeUsageMetadata:
    def __init__(self, prompt_token_count=50, candidates_token_count=10):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class FakeResponse:
    def __init__(self, parsed=None, text=None, model_version="gemini-3.5-flash-lite-001", usage_metadata=None):
        self.parsed = parsed
        self.text = text
        self.model_version = model_version
        self.usage_metadata = usage_metadata if usage_metadata is not None else FakeUsageMetadata()


class FakeModels:
    """Stands in for client.models — records every call's kwargs and
    returns a caller-controlled canned response, or raises a
    caller-controlled exception to simulate a real Gemini API failure."""

    def __init__(self, response=None, raises=None):
        self.calls = []
        self._response = response
        self._raises = raises

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._raises is not None:
            raise self._raises
        return self._response


class FakeClient:
    def __init__(self, response=None, raises=None):
        self.models = FakeModels(response, raises)


def _chunk(chunk_id="c1", content="some content", element_type="text", page_number=1, document_name="doc.pdf", image=None):
    return GeneratorChunk(
        chunk_id=chunk_id,
        content=content,
        element_type=element_type,
        page_number=page_number,
        document_name=document_name,
        image=image,
    )


def _fake_client_error(code: int, message: str) -> ClientError:
    response = httpx.Response(code, json={"error": {"message": message, "status": "SIMULATED"}})
    return ClientError(code, response)


# --- Part 1: fast/mocked --------------------------------------------------


# Acceptance criterion: Verifier.verify(claim, chunk) -> Verdict{verdict, quote} uses Gemini 3.5 Flash-Lite
def test_verifier_verify_claim_chunk_verdict_verdict_quote_uses_gemin():
    parsed = _VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="revenue grew 12%")
    client = FakeClient(response=FakeResponse(parsed=parsed))
    verifier = Verifier(client=client)

    result = verifier.verify("Revenue grew 12%.", _chunk(content="...revenue grew 12% year over year..."))

    assert isinstance(result, Verdict)
    assert result.verdict == VerdictLabel.SUPPORTED
    assert result.quote == "revenue grew 12%"
    assert len(client.models.calls) == 1
    assert client.models.calls[0]["model"] == MODEL == "gemini-3.5-flash-lite"


# Acceptance criterion: Verdict enum: supported | partial | unsupported
def test_verdict_enum_supported_partial_unsupported():
    assert VerdictLabel.SUPPORTED.value == "supported"
    assert VerdictLabel.PARTIAL.value == "partial"
    assert VerdictLabel.UNSUPPORTED.value == "unsupported"
    assert {v.value for v in VerdictLabel} == {"supported", "partial", "unsupported"}


# Acceptance criterion: Returns the supporting quote from the source (or null if unsupported)
def test_returns_the_supporting_quote_from_the_source_or_null_if_unsu():
    chunk = _chunk(content="the document states the exact span that supports this claim clearly")
    supported = FakeResponse(
        parsed=_VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="the exact span that supports this claim")
    )
    verifier = Verifier(client=FakeClient(response=supported))
    result = verifier.verify("a claim", chunk)
    assert result.quote == "the exact span that supports this claim"

    unsupported = FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.UNSUPPORTED, quote=None))
    verifier = Verifier(client=FakeClient(response=unsupported))
    result = verifier.verify("a claim", _chunk())
    assert result.quote is None


def test_quote_is_forced_null_for_unsupported_even_if_model_deviates_and_sets_one():
    # Defensive, not just prompted: the model claiming "unsupported" but
    # still emitting a quote (a prompt-instruction deviation) must not
    # leak a phantom supporting quote onto a citation the verdict itself
    # says is unsupported.
    deviant = FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.UNSUPPORTED, quote="this should not appear"))
    verifier = Verifier(client=FakeClient(response=deviant))

    result = verifier.verify("a claim", _chunk())

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.quote is None


def test_multimodal_verify_passes_chunk_image_content_to_gemini():
    parsed = _VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="see figure")
    client = FakeClient(response=FakeResponse(parsed=parsed))
    verifier = Verifier(client=client)

    fake_png_bytes = b"\x89PNG\r\n\x1a\nfake-image-data"
    figure_chunk = _chunk(element_type="figure", content="Figure 2", image=fake_png_bytes)

    verifier.verify("The chart shows growth.", figure_chunk)

    contents = client.models.calls[0]["contents"]
    image_parts = [p for p in contents if p.inline_data is not None]
    assert len(image_parts) == 1
    assert image_parts[0].inline_data.data == fake_png_bytes
    assert image_parts[0].inline_data.mime_type == "image/png"


# Acceptance criterion: Batches verifications per generate call
def test_batches_verifications_per_generate_call():
    parsed = _VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="some content")
    client = FakeClient(response=FakeResponse(parsed=parsed))
    verifier = Verifier(client=client)

    pairs = [(f"claim {i}", _chunk(chunk_id=f"c{i}")) for i in range(4)]
    results = verifier.verify_batch(pairs)

    assert len(results) == 4
    assert all(isinstance(r, Verdict) for r in results)
    assert len(client.models.calls) == 4


def test_verify_batch_runs_calls_concurrently_not_sequentially():
    parsed = _VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="q")
    verifier = Verifier(client=FakeClient(response=FakeResponse(parsed=parsed)))

    sleep_seconds = 0.3
    call_count = 0

    def slow_verify(claim_text, chunk):
        nonlocal call_count
        call_count += 1
        time.sleep(sleep_seconds)
        return Verdict(verdict=VerdictLabel.SUPPORTED, quote="q", model=MODEL, input_tokens=1, output_tokens=1, latency_ms=1.0)

    verifier.verify = slow_verify

    pairs = [(f"claim {i}", _chunk()) for i in range(4)]
    started = time.monotonic()
    verifier.verify_batch(pairs)
    elapsed = time.monotonic() - started

    assert call_count == 4
    # Sequential would take >= 4 * sleep_seconds (1.2s); real concurrency
    # keeps it close to a single sleep_seconds (0.3s) plus overhead.
    assert elapsed < sleep_seconds * 2, f"expected concurrent execution (~{sleep_seconds}s), took {elapsed:.3f}s"


def test_verify_batch_preserves_input_order_regardless_of_completion_order():
    verifier = Verifier(client=FakeClient())

    def verify_by_index(claim_text, chunk):
        # Later-submitted pairs finish FIRST, to prove ordering isn't
        # accidentally correct just because completion order matches.
        index = int(claim_text.split()[-1])
        time.sleep((4 - index) * 0.05)
        return Verdict(
            verdict=VerdictLabel.SUPPORTED, quote=str(index), model=MODEL, input_tokens=1, output_tokens=1, latency_ms=1.0
        )

    verifier.verify = verify_by_index

    pairs = [(f"claim {i}", _chunk()) for i in range(4)]
    results = verifier.verify_batch(pairs)

    assert [r.quote for r in results] == ["0", "1", "2", "3"]


def test_verify_batch_empty_list_returns_empty_list_without_calling_gemini():
    client = FakeClient()
    verifier = Verifier(client=client)

    assert verifier.verify_batch([]) == []
    assert client.models.calls == []


def test_verify_raises_verification_error_for_empty_claim_text():
    verifier = Verifier(client=FakeClient())

    with pytest.raises(VerificationError):
        verifier.verify("", _chunk())

    with pytest.raises(VerificationError):
        verifier.verify("   ", _chunk())


# --- Part 1b: fail-safe behavior (item 6 — the most important behavior
# in this feature). Each of these proves an unverifiable claim can NEVER
# be silently treated as verified, tested concretely rather than assumed.


def test_verify_fails_safe_to_unsupported_when_gemini_api_call_raises():
    client = FakeClient(raises=_fake_client_error(429, "rate limited"))
    verifier = Verifier(client=client)

    result = verifier.verify("some claim", _chunk())

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.quote is None
    assert result.error is not None
    assert "rate limited" in result.error or "429" in result.error or "ClientError" in result.error


def test_verify_fails_safe_to_unsupported_when_response_parsed_is_none():
    # Simulates the SDK's own documented behavior (google/genai/types.py):
    # a malformed/non-schema-conforming response leaves response.parsed
    # as None WITHOUT raising — verified against installed SDK source,
    # see .agent/api-docs/gemini.md. Code that assumes parsed is always
    # populated after a successful call would crash here or worse.
    client = FakeClient(response=FakeResponse(parsed=None, text="not valid json for the schema"))
    verifier = Verifier(client=client)

    result = verifier.verify("some claim", _chunk())

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.quote is None
    assert result.error is not None


def test_verify_never_raises_or_crashes_on_a_broken_gemini_call():
    # The explicit, structural guarantee: verify() itself never lets a
    # broken underlying call propagate as an exception OR as anything
    # other than UNSUPPORTED — a caller that only reads `.verdict` and
    # never checks `.error` still gets the safe outcome by construction,
    # not by convention it has to remember to uphold.
    for client in (
        FakeClient(raises=_fake_client_error(500, "server error")),
        FakeClient(response=FakeResponse(parsed=None)),
    ):
        result = Verifier(client=client).verify("claim", _chunk())
        assert result.verdict == VerdictLabel.UNSUPPORTED
        assert result.quote is None


def test_verify_batch_one_failing_pair_does_not_contaminate_others():
    call_log = []

    class SelectivelyFailingModels:
        def generate_content(self, *, model, contents, config):
            # The failing claim is identifiable by its content.
            question_text = contents[-1].text
            call_log.append(question_text)
            if "BROKEN" in question_text:
                raise _fake_client_error(503, "unavailable")
            return FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="some content"))

    class SelectivelyFailingClient:
        def __init__(self):
            self.models = SelectivelyFailingModels()

    verifier = Verifier(client=SelectivelyFailingClient())
    pairs = [
        ("a working claim", _chunk(chunk_id="ok1")),
        ("a BROKEN claim", _chunk(chunk_id="broken")),
        ("another working claim", _chunk(chunk_id="ok2")),
    ]

    results = verifier.verify_batch(pairs)

    assert results[0].verdict == VerdictLabel.SUPPORTED
    assert results[0].error is None
    assert results[1].verdict == VerdictLabel.UNSUPPORTED
    assert results[1].error is not None
    assert results[2].verdict == VerdictLabel.SUPPORTED
    assert results[2].error is None
    assert len(call_log) == 3


# --- Part 1c: 2026-07-24 self-audit findings ------------------------------
#
# This audit specifically stress-tested whether "fails safe" generalizes
# across failure SHAPES, the way FEAT-010's bracket-parsing fix initially
# didn't generalize across delimiter shapes. It found three real gaps,
# fixed here and locked in below: (1) a genuine timeout/transport failure
# raised a raw httpx exception that `except APIError` never caught,
# crashing verify() instead of failing safe; (2) a SUPPORTED/PARTIAL
# verdict's quote was trusted as-is with no check it actually appears in
# the source — a fabricated-but-plausible quote passed straight through
# as "verified," proven live before the fix existed; (4) confirmed
# response_schema's enum enforcement is genuinely client-side (pydantic,
# on the raw JSON text) not merely "the API behaves" — locked in as a
# regression test in case a future change loosens `VerdictLabel` typing.


def test_verify_fails_safe_when_the_call_times_out_at_the_transport_layer():
    # A self-audit found this crashed verify() with an uncaught
    # httpx.ReadTimeout before the httpx.HTTPError except clause existed
    # — genai's own _api_client.py calls httpx directly with no
    # try/except, so a timeout is raised BEFORE the SDK ever gets a
    # response to wrap into APIError. Confirmed live against the real
    # exception type, not a generic stand-in.
    class TimeoutModels:
        def generate_content(self, *, model, contents, config):
            raise httpx.ReadTimeout("the request timed out")

    class TimeoutClient:
        def __init__(self):
            self.models = TimeoutModels()

    result = Verifier(client=TimeoutClient()).verify("some claim", _chunk())

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.quote is None
    assert result.error is not None


def test_verify_fails_safe_when_connection_is_refused():
    # A different httpx.HTTPError subtree (connection-level, not
    # timeout-level) — confirms the except clause catches the base class
    # broadly, not just the one timeout subclass exercised above.
    class UnreachableModels:
        def generate_content(self, *, model, contents, config):
            raise httpx.ConnectError("connection refused")

    class UnreachableClient:
        def __init__(self):
            self.models = UnreachableModels()

    result = Verifier(client=UnreachableClient()).verify("some claim", _chunk())

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.error is not None


# Item 2 — the priority finding. Proven live before this check existed:
# a mocked verdict=SUPPORTED response with a plausible-sounding quote
# that does not appear anywhere in the real chunk content passed straight
# through as "verified" with no check at all.
def test_verify_fails_safe_when_the_returned_quote_is_not_actually_in_the_source():
    chunk = _chunk(content="Table 19: Human Development Index (HDI)\n\n| Country | 2010 |\n| Angola | 4.42 |")
    fabricated_quote = "Angola achieved an HDI of 4.42, ranking it among the top improving nations."
    client = FakeClient(response=FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote=fabricated_quote)))

    result = Verifier(client=client).verify("Angola achieved a top-improving HDI of 4.42.", chunk)

    assert result.verdict == VerdictLabel.UNSUPPORTED, "a fabricated quote must never let a SUPPORTED verdict through"
    assert result.quote is None
    assert result.error is not None
    assert "not found in source" in result.error


def test_verify_fails_safe_when_a_partial_verdicts_quote_is_fabricated():
    # The grounding check must apply to PARTIAL too, not just SUPPORTED —
    # both carry a quote per the schema.
    chunk = _chunk(content="real content about topic X")
    client = FakeClient(
        response=FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.PARTIAL, quote="a quote that was never in the source"))
    )

    result = Verifier(client=client).verify("a claim", chunk)

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.quote is None


def test_verify_accepts_a_grounded_quote_despite_whitespace_differences():
    # The grounding check normalizes whitespace rather than requiring a
    # byte-for-byte substring match, since real chunk content (e.g.
    # padded markdown tables) can differ from the model's reproduction by
    # whitespace alone without the quote being fabricated — confirmed
    # against the real adversarial fixture's own table formatting.
    chunk = _chunk(content="Angola      | -      | -      |   4.42 |   4.42 |")
    client = FakeClient(
        response=FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="Angola | - | - | 4.42 | 4.42 |"))
    )

    result = Verifier(client=client).verify("Angola's value was 4.42.", chunk)

    assert result.verdict == VerdictLabel.SUPPORTED
    assert result.quote == "Angola | - | - | 4.42 | 4.42 |"


# Item 4 — confirming response_schema's enum enforcement is genuinely
# client-side (pydantic validating the raw JSON text locally), not merely
# "the API happens to behave." A garbage verdict string is rejected here
# regardless of what any future API version might send.
def test_out_of_enum_verdict_string_is_rejected_by_schema_validation_not_api_behavior():
    with pytest.raises(pydantic.ValidationError):
        _VerdictResponse.model_validate_json('{"verdict": "probably_true", "quote": "something"}')


def test_verify_fails_safe_when_the_sdk_would_receive_an_out_of_enum_verdict():
    # End-to-end version of the check above: even if a future API
    # response somehow contained an out-of-enum verdict string, the SDK's
    # own documented behavior (silently swallowing the resulting
    # pydantic.ValidationError and leaving response.parsed = None — see
    # .agent/api-docs/gemini.md) means verify() sees exactly the same
    # response.parsed is None state already handled above — not a new,
    # unhandled shape.
    client = FakeClient(response=FakeResponse(parsed=None, text='{"verdict": "probably_true", "quote": "x"}'))

    result = Verifier(client=client).verify("claim", _chunk())

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.error is not None


# Item 3 — adversarial batch poisoning: TWO poisoned pairs, at
# different/boundary positions (first and last of five), confirm every
# surrounding valid pair still verifies correctly regardless of a
# poisoned pair's position in the batch.
@pytest.mark.parametrize(
    "poisoned_positions",
    [
        (0, 4),  # first and last — index-boundary risk
        (1, 3),  # interior, non-adjacent
        (0, 1),  # adjacent, both at the start
    ],
)
def test_verify_batch_survives_two_poisoned_pairs_at_any_position(poisoned_positions):
    call_log = []

    class SelectivelyFailingModels:
        def generate_content(self, *, model, contents, config):
            claim_text = contents[-1].text
            call_log.append(claim_text)
            if "POISON" in claim_text:
                raise _fake_client_error(503, "simulated outage")
            return FakeResponse(parsed=_VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="some content"))

    class SelectivelyFailingClient:
        def __init__(self):
            self.models = SelectivelyFailingModels()

    pairs = []
    for i in range(5):
        label = "POISON" if i in poisoned_positions else "clean"
        pairs.append((f"{label} claim {i}", _chunk(chunk_id=f"c{i}")))

    results = Verifier(client=SelectivelyFailingClient()).verify_batch(pairs)

    assert len(results) == 5
    assert len(call_log) == 5
    for i, result in enumerate(results):
        if i in poisoned_positions:
            assert result.verdict == VerdictLabel.UNSUPPORTED, f"position {i} (poisoned) should fail safe"
            assert result.error is not None
        else:
            assert result.verdict == VerdictLabel.SUPPORTED, f"position {i} (clean) should verify normally"
            assert result.error is None


# --- Part 2: real API (opt-in only) --------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_VERIFIER_TEST") != "1",
    reason="set RUN_REAL_VERIFIER_TEST=1 to run real Gemini Flash-Lite verification calls",
)
def test_real_gemini_flash_lite_verifies_a_clearly_supported_claim():
    verifier = Verifier()  # real client, reads GEMINI_API_KEY from env

    chunk = _chunk(content="Docify's integration test constant is exactly 8675309.", document_name="integration-test.txt")

    result = verifier.verify("Docify's integration test constant is 8675309.", chunk)

    print("\n" + "=" * 90)
    print("FEAT-011 real Gemini Flash-Lite verify() call — clearly supported claim")
    print("=" * 90)
    print(f"verdict={result.verdict} quote={result.quote!r}")
    print(f"model={result.model} input_tokens={result.input_tokens} output_tokens={result.output_tokens} latency_ms={result.latency_ms:.1f}")
    print("=" * 90)

    assert result.verdict == VerdictLabel.SUPPORTED
    assert result.quote is not None
    assert "8675309" in result.quote
    assert result.error is None
    assert result.input_tokens > 0
    assert result.output_tokens > 0


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_VERIFIER_TEST") != "1",
    reason="set RUN_REAL_VERIFIER_TEST=1 to run a real (deliberately-failing) Gemini API call",
)
def test_real_gemini_call_with_invalid_api_key_still_fails_safe():
    # Item 6, proven against a REAL network failure, not a mocked one —
    # a genuinely invalid API key forces a real auth error from Google's
    # actual endpoint. Costs no quota (fails before any generation).
    from google import genai

    verifier = Verifier(client=genai.Client(api_key="invalid-key-for-testing-fail-safe-behavior"))

    result = verifier.verify("some claim", _chunk())

    print("\n" + "=" * 90)
    print("FEAT-011 real Gemini API call with an invalid key — must fail safe, not crash or pass through")
    print("=" * 90)
    print(f"verdict={result.verdict} quote={result.quote!r} error={result.error!r}")
    print("=" * 90)

    assert result.verdict == VerdictLabel.UNSUPPORTED
    assert result.quote is None
    assert result.error is not None


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_VERIFIER_TEST") != "1",
    reason="set RUN_REAL_VERIFIER_TEST=1 to run a real batch of 5 concurrent Gemini Flash-Lite verify calls",
)
def test_real_batch_latency_under_load_with_five_concurrent_claims():
    # Item 6 — FEAT-011's own single-call numbers don't say anything
    # about what /query's actual end-to-end response time looks like once
    # verify_batch() fires several real calls concurrently for one
    # answer. Real network conditions (shared connection pool, provider-
    # side concurrency limits) can behave differently under real
    # concurrent load than under one call at a time.
    verifier = Verifier()

    chunk = _chunk(content="Docify's integration test constant is exactly 8675309.", document_name="integration-test.txt")
    # All 5 are the same true claim — this test measures concurrent
    # latency under real load, not verdict diversity (that's covered by
    # the adversarial fixture test above).
    pairs = [("Docify's integration test constant is 8675309.", chunk) for _ in range(5)]

    started = time.perf_counter()
    results = verifier.verify_batch(pairs)
    total_latency_ms = (time.perf_counter() - started) * 1000

    print("\n" + "=" * 90)
    print("FEAT-011 real batch latency — 5 concurrent verify() calls, real Gemini Flash-Lite")
    print("=" * 90)
    for i, result in enumerate(results):
        print(
            f"[{i}] verdict={result.verdict.value} quote={result.quote!r} "
            f"input_tokens={result.input_tokens} output_tokens={result.output_tokens} latency_ms={result.latency_ms:.1f}"
        )
    per_call_latencies = [r.latency_ms for r in results]
    print(f"\ntotal batch wall-clock latency: {total_latency_ms:.1f}ms")
    print(f"per-call latencies: {[f'{l:.1f}' for l in per_call_latencies]}")
    print(f"sum of per-call latencies (would-be sequential time): {sum(per_call_latencies):.1f}ms")
    print("=" * 90)

    assert len(results) == 5
    assert all(r.error is None for r in results)
    assert all(r.verdict == VerdictLabel.SUPPORTED for r in results)
    # Concurrency should keep total wall-clock well under the sum of
    # per-call latencies (true sequential execution) — not asserting a
    # tight bound since real network variance is real, just confirming
    # the batch genuinely overlaps rather than serializing.
    assert total_latency_ms < sum(per_call_latencies)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_VERIFIER_TEST") != "1",
    reason="set RUN_REAL_VERIFIER_TEST=1 to run a real adversarial verification fixture against table_heavy.pdf",
)
def test_adversarial_fixture_against_real_table_heavy_pdf_content():
    # A real fixture, not synthetic strings: real Docling-parsed content
    # from table_heavy.pdf (no embeddings/Voyage needed — verification
    # doesn't retrieve, so this costs no Voyage quota). Locates the real
    # HDI table chunk (Table 19, Angola row, value 4.42 in both the 2000
    # and 2010 columns — the same real content FEAT-009/010 already used)
    # and runs four adversarially-constructed claims against it.
    from services.chunker import Chunker
    from services.parser import Parser

    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parsed = Parser().parse(pdf_bytes)
    chunks = Chunker().chunk(parsed)

    hdi_chunk = next(c for c in chunks if "Angola" in c.content and "4.42" in c.content)
    generator_chunk = GeneratorChunk(
        chunk_id="hdi-chunk",
        content=hdi_chunk.content,
        element_type=hdi_chunk.element_type.value,
        page_number=min(hdi_chunk.page_numbers),
        document_name="table_heavy.pdf",
        image=None,
    )

    claims = {
        "fully_supported": "Angola's Human Development Index value in 2010 was 4.42.",
        "contradicted": "Angola's Human Development Index value in 2010 was 8.15.",
        "overreaches_partial": (
            "Angola's Human Development Index improved significantly between 2000 and 2010, "
            "driven by major economic reforms and foreign investment."
        ),
        "fabricated_unrelated": "The company's total revenue grew by 45% due to new product launches in Asia.",
    }

    verifier = Verifier()
    results = {}

    print("\n" + "=" * 90)
    print("FEAT-011 adversarial fixture — real table_heavy.pdf HDI chunk, four constructed claims")
    print("=" * 90)
    print(f"chunk content: {generator_chunk.content!r}")

    for i, (label, claim) in enumerate(claims.items()):
        if i > 0:
            time.sleep(5)  # light pacing between real Flash-Lite calls
        result = verifier.verify(claim, generator_chunk)
        results[label] = result
        print(f"\n[{label}] claim: {claim!r}")
        print(f"  -> verdict={result.verdict.value} quote={result.quote!r}")
        print(f"     model={result.model} input_tokens={result.input_tokens} output_tokens={result.output_tokens} latency_ms={result.latency_ms:.1f}")

    print("\n" + "=" * 90)

    assert results["fully_supported"].verdict == VerdictLabel.SUPPORTED
    assert results["fully_supported"].quote is not None

    assert results["fabricated_unrelated"].verdict == VerdictLabel.UNSUPPORTED
    assert results["fabricated_unrelated"].quote is None

    # The contradicted claim (right topic, wrong number) must NOT be
    # accepted as fully supported — whether the model labels it
    # "unsupported" or "partial" is itself part of what this test is
    # reporting on, not something to force with a narrow assertion.
    assert results["contradicted"].verdict != VerdictLabel.SUPPORTED


@pytest.mark.skipif(
    os.environ.get("RUN_VERIFICATION_QUALITY_TEST") != "1",
    reason="set RUN_VERIFICATION_QUALITY_TEST=1 to run real retrieval+generation+verification end-to-end (slow, uses quota)",
)
def test_verifies_a_real_generated_answers_citation_end_to_end(admin, user_a):
    # Item 5 — the case that matters most: does a positionally-correct
    # citation (FEAT-010 already proved this) actually hold up under
    # independent factual verification, or is that distinction only
    # theoretical? Reuses FEAT-009/010's exact table_heavy.pdf pipeline
    # for one real question.
    from services.chunker import Chunker
    from services.embedder import Embedder
    from services.generator import Generator
    from services.parser import Parser
    from services.retriever import Retriever

    user_id, _token = user_a

    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parsed = Parser().parse(pdf_bytes)
    chunks = Chunker().chunk(parsed)
    vectors = Embedder().embed(chunks)

    document_id = (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": "table_heavy.pdf",
                "storage_path": f"uploads/{user_id}/table_heavy.pdf",
                "mime_type": "application/pdf",
                "size_bytes": len(pdf_bytes),
            }
        )
        .execute()
        .data[0]["id"]
    )

    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk.chunk_index,
                "element_type": chunk.element_type.value,
                "page_number": min(chunk.page_numbers),
                "content": chunk.content,
                "embedding": vector,
            }
        )
    admin.table("chunks").insert(rows).execute()

    for image in (c.image for c in chunks if c.image is not None):
        image.close()

    retriever = Retriever(client=admin)
    generator = Generator()
    verifier = Verifier()

    time.sleep(25)  # real Voyage free-tier pacing, same constraint as FEAT-009/010

    question = "What is Angola's Human Development Index value in 2010?"
    retrieved = retriever.retrieve(question, [document_id], user_id, k=5)
    generator_chunks = [
        GeneratorChunk(
            chunk_id=r.chunk_id, content=r.content, element_type=r.element_type, page_number=r.page, document_name=r.document_name
        )
        for r in retrieved
    ]

    gen_result = generator.generate(question, generator_chunks)

    print("\n" + "=" * 90)
    print("FEAT-011 end-to-end — real retrieval + real generation + real verification")
    print("=" * 90)
    print(f"answer: {gen_result.answer!r}")
    print(f"cited_indices={gen_result.cited_indices}")

    assert gen_result.cited_indices, "generation must have cited something for this test to verify anything"
    cited_position = gen_result.cited_indices[0]
    cited_chunk = generator_chunks[cited_position - 1]

    # Item 3's scope boundary: this test hand-extracts the claim text
    # associated with the citation, standing in for the [N]-to-claim
    # resolution FEAT-012 will do for real — verify() itself only ever
    # takes an already-resolved (claim, chunk) pair.
    claim_text = "Angola's Human Development Index (HDI) value in 2010 was 4.42."

    verdict_result = verifier.verify(claim_text, cited_chunk)

    print(f"cited chunk (position {cited_position}) content: {cited_chunk.content!r}")
    print(f"claim verified: {claim_text!r}")
    print(f"-> verdict={verdict_result.verdict.value} quote={verdict_result.quote!r}")
    print(
        f"   model={verdict_result.model} input_tokens={verdict_result.input_tokens} "
        f"output_tokens={verdict_result.output_tokens} latency_ms={verdict_result.latency_ms:.1f}"
    )
    print("=" * 90)

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()

    assert verdict_result.verdict in (VerdictLabel.SUPPORTED, VerdictLabel.PARTIAL)
    assert verdict_result.quote is not None
