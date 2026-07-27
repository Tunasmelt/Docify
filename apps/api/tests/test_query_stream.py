# Tests for [FEAT-016] `POST /query/stream` — the SSE streaming variant
# of `/query`.
#
# The one thing that must NEVER differ between this endpoint and the
# already-proven non-streaming `/query` (test_query.py) is which
# citations get kept/dropped and how markers get stripped — that's this
# project's core safety property (FEAT-011/012), and streaming changes
# HOW content reaches the client, not the verification rules themselves.
# Every citation-safety test below is a direct SSE-shaped mirror of an
# existing test_query.py test, re-run against the streaming path
# specifically, per this feature's own task brief (priority item).
#
# Real local Supabase (Auth, Postgres) throughout — Retriever/Generator/
# Verifier faked via FastAPI dependency overrides, same shape as
# test_query.py, so fake citations still reference real chunk rows from
# a real ingested document.

import json

import pytest

from main import app
from routes import query
from services.generator import GenerateStreamResult, GenerationError
from services.verifier import VerdictLabel
from tests.test_query import (
    FakeRetriever,
    RetrievedChunk,
    _clear_overrides,
    _ingest_doc_with_content,
    _override,
    _real_chunk_row,
    _verdict,
)


class FakeStreamingGenerator:
    """generate_stream() fake matching Generator's real contract: yields
    each str delta in order, then exactly one final GenerateStreamResult."""

    def __init__(self, deltas: list[str], final: GenerateStreamResult):
        self._deltas = deltas
        self._final = final
        self.calls: list[dict] = []

    async def generate_stream(self, question, chunks, history=None):
        self.calls.append({"question": question, "chunks": chunks, "history": history})
        for delta in self._deltas:
            yield delta
        yield self._final


class FakeStreamingGeneratorRaisingMidStream:
    """Yields some real deltas, then raises GenerationError partway
    through — the "generation fails mid-stream" case from this feature's
    task brief (item 6)."""

    def __init__(self, deltas: list[str], exc: Exception):
        self._deltas = deltas
        self._exc = exc

    async def generate_stream(self, question, chunks, history=None):
        for delta in self._deltas:
            yield delta
        raise self._exc


class FakeVerifierRaising:
    """Verification failing AFTER streaming completes but BEFORE the
    stream closes — the second mid-stream-failure case from this
    feature's task brief (item 6). Deliberately a plain, undocumented
    exception (not one Verifier itself already fails safe on), since the
    real Verifier already converts Gemini-call failures into a safe
    UNSUPPORTED Verdict — this exercises the case where verify_batch()
    itself breaks, which /query/stream must still surface as a visible
    error, not a hang."""

    def verify_batch(self, pairs):
        raise RuntimeError("verifier exploded unexpectedly")


def _parse_sse(response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = None
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if event_name is not None:
                data = json.loads("\n".join(data_lines)) if data_lines else {}
                events.append((event_name, data))
            event_name, data_lines = None, []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
    return events


@pytest.fixture(autouse=True)
def _clear_query_stream_overrides_after_each_test():
    yield
    _clear_overrides()


def _retrieved_chunk(document_id: str, chunk_row: dict) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_row["id"],
        content=chunk_row["content"],
        page=1,
        document_id=document_id,
        document_name="doc.pdf",
        document_mime_type="application/pdf",
        element_type="text",
        score=0.9,
    )


# Acceptance: full event sequence retrieving -> token* -> verifying ->
# citations-resolved -> done, and a supported citation is kept, matching
# test_query.py's equivalent non-streaming assertion exactly.
def test_stream_full_event_sequence_and_supported_citation_kept(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12% year over year.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer="Revenue grew 12% [1].",
        cited_indices=[1],
        hallucinated_markers=[],
        model="gemini-3.6-flash",
        input_tokens=100,
        output_tokens=20,
        latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["Revenue grew ", "12% [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED, "Revenue grew 12%") for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "What was revenue growth?", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = _parse_sse(response)

    event_names = [name for name, _ in events]
    assert event_names == ["retrieving", "token", "token", "verifying", "citations-resolved", "done"]

    token_text = "".join(data["text"] for name, data in events if name == "token")
    assert token_text == "Revenue grew 12% [1]."

    resolved = next(data for name, data in events if name == "citations-resolved")
    assert resolved["answer"] == "Revenue grew 12% [1]."
    assert len(resolved["citations"]) == 1
    assert resolved["citations"][0]["marker"] == 1
    assert resolved["citations"][0]["chunk_id"] == chunk_row["id"]
    assert resolved["citations"][0]["verdict"] == "supported"
    assert "conversation_id" in resolved and resolved["conversation_id"]
    assert "message_id" in resolved and resolved["message_id"]

    done = next(data for name, data in events if name == "done")
    assert done["metadata"]["cited_count"] == 1
    assert done["metadata"]["retrieved_count"] == 1


# Acceptance: unsupported citations dropped from citations-resolved,
# marker stripped from the resolved answer text — same rule as
# test_query.py's test_unsupported_citations_are_dropped_from_response_markers_stri.
def test_stream_unsupported_citations_dropped_and_markers_stripped(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%. The forecast is bright.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer="Revenue grew 12% [1]. The outlook is fabricated [1].",
        cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["Revenue grew 12% [1]. The outlook is fabricated [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.UNSUPPORTED, None) for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        events = _parse_sse(response)

    resolved = next(data for name, data in events if name == "citations-resolved")
    assert resolved["citations"] == []
    assert "[1]" not in resolved["answer"]


# Acceptance: partial-verdict citations are KEPT, marker stays — same
# rule as test_query.py's test_partial_verdict_citations_are_kept_not_dropped.
def test_stream_partial_verdict_citations_kept_not_dropped(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Growth was broad-based this quarter.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer="Growth was driven by international demand [1].",
        cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["Growth was driven by international demand [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.PARTIAL, "Growth was broad-based") for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        events = _parse_sse(response)

    resolved = next(data for name, data in events if name == "citations-resolved")
    assert len(resolved["citations"]) == 1
    assert resolved["citations"][0]["verdict"] == "partial"
    assert "[1]" in resolved["answer"], "partial citations must keep their marker — never dropped like unsupported"

    done = next(data for name, data in events if name == "done")
    assert done["metadata"]["cited_count"] == 1


# Acceptance: an unsupported citation is still PERSISTED for audit even
# though dropped from the resolved response — same rule as
# test_query.py's test_unsupported_citation_is_still_persisted_for_audit_even_though_dropped_from_response.
def test_stream_unsupported_citation_still_persisted_for_audit(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Real content.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer="A fabricated claim [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["A fabricated claim [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.UNSUPPORTED, None) for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        events = _parse_sse(response)

    resolved = next(data for name, data in events if name == "citations-resolved")
    assert resolved["citations"] == []

    citations = admin.table("citations").select("*").eq("message_id", resolved["message_id"]).execute().data
    assert len(citations) == 1
    assert citations[0]["verdict"] == "unsupported"


# Acceptance (task brief item 6): generation failing PARTWAY through the
# stream emits an `error` event and stops — never a hang, never
# verifying/citations-resolved/done after a partial answer.
def test_stream_generation_failure_mid_stream_emits_error_and_stops(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Some real content.")
    chunk_row = _real_chunk_row(admin, document_id)

    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGeneratorRaisingMidStream(
            ["Partial answer chunk one", " chunk two"], GenerationError("simulated Gemini failure mid-stream")
        ),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: (_ for _ in ()).throw(AssertionError("verify_batch must never be called after a generation failure")))})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200  # SSE: failure is IN the stream, not an HTTP status
        events = _parse_sse(response)

    event_names = [name for name, _ in events]
    assert event_names == ["retrieving", "token", "token", "error"]
    error_data = events[-1][1]
    assert error_data["code"] == "GENERATE_FAILED"
    assert "verifying" not in event_names
    assert "citations-resolved" not in event_names
    assert "done" not in event_names


# Acceptance (task brief item 6): verification/persistence failing AFTER
# generation completes but BEFORE the stream closes also emits a visible
# `error` event and stops — never citations-resolved/done with no
# explanation.
def test_stream_verification_failure_emits_error_and_stops(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Some real content.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer="A real answer [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["A real answer [1]."], final),
        verifier=FakeVerifierRaising(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response)

    event_names = [name for name, _ in events]
    assert event_names == ["retrieving", "token", "verifying", "error"]
    error_data = events[-1][1]
    assert error_data["code"] == "VERIFY_FAILED"
    assert "citations-resolved" not in event_names
    assert "done" not in event_names
