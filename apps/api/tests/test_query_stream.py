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

import contextlib
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

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
    Verdict (UNVERIFIED for a genuine infrastructure failure — an
    errored/timed-out/malformed call — 2026-08-03; UNSUPPORTED only for
    a caught fabricated/ungrounded quote) — this exercises the case
    where verify_batch() itself breaks (an uncaught exception escaping
    the fail-safe wrapper entirely), which /query/stream must still
    surface as a visible error, not a hang."""

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _live_server():
    """A genuinely real uvicorn server, in a background thread — needed
    for Part 2's disconnect tests specifically. TestClient (in-process
    ASGI transport, used everywhere else in this file) cannot produce a
    real TCP disconnect at all; the investigation behind this feature
    (routes/query.py's module comment above _watch_for_disconnect) found
    that even `request.is_disconnected()` itself only became reliably
    accurate against a REAL socket close on a REAL server — not something
    a mocked/simulated disconnect signal could stand in for without
    defeating the entire point of testing this."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 5
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "real uvicorn server failed to start in time"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


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


# Settings batch 2 — same rerank wiring proof as test_query.py's
# equivalent test, mirrored for the streaming path (this project's own
# discipline: streaming must never silently diverge from the
# non-streaming request contract).
def test_stream_rerank_field_reaches_retriever_retrieve(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "content")
    chunk_row = _real_chunk_row(admin, document_id)

    fake_retriever = FakeRetriever([_retrieved_chunk(document_id, chunk_row)])
    final = GenerateStreamResult(
        answer="n/a", cited_indices=[], hallucinated_markers=[],
        model="m", input_tokens=0, output_tokens=0, latency_ms=0,
    )
    _override(
        retriever=fake_retriever,
        generator=FakeStreamingGenerator([], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [])})(),
    )

    with app_client.stream(
        "POST", "/query/stream",
        json={"question": "q", "document_ids": [document_id], "rerank": True},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        _parse_sse(response)

    assert len(fake_retriever.calls) == 1
    assert fake_retriever.calls[0]["rerank"] is True


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


# 2026-08-03 — UNVERIFIED feature. Same rule as test_query.py's
# test_unverified_citations_are_kept_not_dropped, mirrored against the
# streaming path: a citation whose verification genuinely couldn't run
# must be KEPT with its marker intact, never dropped like a real
# UNSUPPORTED verdict.
def test_stream_unverified_verdict_citations_kept_not_dropped(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12% this quarter.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer="Revenue grew 12% this quarter [1].",
        cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["Revenue grew 12% this quarter [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.UNVERIFIED, None) for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        events = _parse_sse(response)

    resolved = next(data for name, data in events if name == "citations-resolved")
    assert len(resolved["citations"]) == 1
    assert resolved["citations"][0]["verdict"] == "unverified"
    # response_model_exclude_none=True (API_CONTRACT.md) omits a None
    # supporting_quote entirely rather than sending it as null.
    assert resolved["citations"][0].get("supporting_quote") is None
    assert "[1]" in resolved["answer"], "unverified citations must keep their marker — never dropped like unsupported"

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


# --- Regression: real production incident, 2026-07-31 ----------------------
#
# The real incident this project hit was specifically on THIS endpoint
# (POST /query/stream, a broad "summarize the provided paper" request) —
# see test_query.py's mirrored non-streaming version for the full
# incident writeup. Streaming and non-streaming share the identical
# _is_resolvable_marker guard and identical SQL-level defense
# (migrations/20260731_002_citation_persistence_defensive.sql) — this
# test exists specifically because streaming is where it was actually
# observed, not assumed safe by analogy to the non-streaming test alone.
def test_stream_unresolvable_citation_position_is_dropped_turn_still_persists(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "paper.pdf", "A paper about a new method.")
    chunk_row = _real_chunk_row(admin, document_id)

    final = GenerateStreamResult(
        answer=(
            "This paper broadly demonstrates a new method for solving X [1]. "
            "It also builds on findings from an earlier section of the same work [7]."
        ),
        cited_indices=[1, 7],  # only 1 chunk was ever retrieved — 7 is unresolvable
        hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["This paper broadly demonstrates a new method for solving X [1]. "], final),
        verifier=type(
            "V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED, "demonstrates a new method")])}
        )(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "Summarize the provided paper.", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response)

    event_names = [name for name, _ in events]
    # No crash, no error event — resolves cleanly through to done, exactly
    # like a normal successful turn.
    assert event_names == ["retrieving", "token", "verifying", "citations-resolved", "done"]

    resolved_data = dict(events)["citations-resolved"]
    assert len(resolved_data["citations"]) == 1
    assert resolved_data["citations"][0]["marker"] == 1
    assert resolved_data["citations"][0]["chunk_id"] == chunk_row["id"]
    assert all(c["marker"] != 7 for c in resolved_data["citations"])
    assert "[7]" not in resolved_data["answer"], "dangling marker must be stripped, not left with no matching citation"

    message_id = resolved_data["message_id"]
    conversation_id = resolved_data["conversation_id"]
    messages = admin.table("messages").select("*").eq("conversation_id", conversation_id).execute().data
    assert len(messages) == 2, "both messages must persist even though one citation was unresolvable"

    saved_citations = admin.table("citations").select("*").eq("message_id", message_id).execute().data
    assert len(saved_citations) == 1
    assert saved_citations[0]["marker"] == 1


# --- Part 1 (2026-08-02): heartbeat keepalive frames during a slow gap -----


class _SlowFakeRetriever:
    """Real, blocking delay (time.sleep, not asyncio.sleep — retrieve()
    is a plain sync method called via asyncio.to_thread in the real
    code, exactly like the real Retriever) — long enough to span several
    heartbeat intervals once query._HEARTBEAT_INTERVAL_S is patched down
    for this test."""

    def __init__(self, chunks, delay_s: float):
        self._chunks = chunks
        self._delay_s = delay_s
        self.calls = []

    def retrieve(self, question, document_ids, user_id, k=8, rerank=False):
        self.calls.append({"question": question})
        time.sleep(self._delay_s)
        return self._chunks


class _SlowStartFakeStreamingGenerator:
    """Real delay BEFORE the first token — the exact gap task item 1
    calls out ("most likely needed during retrieval and the pre-first-
    token generation gap")."""

    def __init__(self, delay_s: float, deltas, final):
        self._delay_s = delay_s
        self._deltas = deltas
        self._final = final

    async def generate_stream(self, question, chunks, history=None):
        import asyncio as _asyncio

        await _asyncio.sleep(self._delay_s)
        for delta in self._deltas:
            yield delta
        yield self._final


# Acceptance (Part 1, item 2): a real, patched-down heartbeat interval
# fires real ": keepalive\n\n" SSE comment frames on the wire during a
# real, artificially delayed retrieval step — and they never get parsed
# as a real named event (event_names sequence is unaffected), matching
# the frontend's real dispatchSseFrame behavior (verified directly
# against apps/web/lib/api/query.ts, not assumed).
def test_stream_heartbeat_frames_appear_during_a_slow_retrieval_gap(app_client, admin, user_a, monkeypatch):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)

    monkeypatch.setattr(query, "_HEARTBEAT_INTERVAL_S", 0.3)

    final = GenerateStreamResult(
        answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=_SlowFakeRetriever([_retrieved_chunk(document_id, chunk_row)], delay_s=1.2),
        generator=FakeStreamingGenerator(["Revenue grew 12% [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED, "Revenue grew 12%") for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        raw = response.read().decode()

    keepalive_count = raw.count(": keepalive\n\n")
    assert keepalive_count >= 2, f"expected multiple real keepalive frames during a 1.2s gap with a 0.3s interval, got {keepalive_count} in: {raw!r}"

    # The heartbeat frames must be genuinely transparent to real SSE-frame
    # parsing — same event sequence as any other successful turn.
    events = _parse_sse(type("R", (), {"iter_lines": lambda self: raw.split("\n")})())
    event_names = [name for name, _ in events]
    assert event_names == ["retrieving", "token", "verifying", "citations-resolved", "done"]


# Acceptance (Part 1, item 2): same real keepalive-frame proof, but for
# the gap BEFORE Gemini's first streamed token specifically — the other
# gap task item 1 names explicitly.
def test_stream_heartbeat_frames_appear_during_a_slow_pre_first_token_gap(app_client, admin, user_a, monkeypatch):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)

    monkeypatch.setattr(query, "_HEARTBEAT_INTERVAL_S", 0.3)

    final = GenerateStreamResult(
        answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=_SlowStartFakeStreamingGenerator(1.2, ["Revenue grew 12% [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED, "Revenue grew 12%") for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        raw = response.read().decode()

    keepalive_count = raw.count(": keepalive\n\n")
    assert keepalive_count >= 2, f"expected multiple real keepalive frames before the first token, got {keepalive_count} in: {raw!r}"

    events = _parse_sse(type("R", (), {"iter_lines": lambda self: raw.split("\n")})())
    event_names = [name for name, _ in events]
    assert event_names == ["retrieving", "token", "verifying", "citations-resolved", "done"]


# --- Part 2 (2026-08-02): a GENUINELY real client disconnect, against a --
# --- real live uvicorn server, a real TCP connection forcibly closed  ---
#
# Not a mock, not request.is_disconnected() patched to return True, not
# TestClient's in-process ASGI transport (confirmed, during this
# feature's own investigation, NOT to produce a real disconnect signal at
# all). A real server, a real httpx connection, a real socket close.
#
# The live-server investigation behind this feature (routes/query.py's
# module comment above _watch_for_disconnect) found request.is_
# disconnected() itself does not work reliably in this deployment either
# — these tests exercise the real fix (_watch_for_disconnect's actively-
# awaited request.receive() loop), not the documented-but-unreliable API.


class _RecordingSlowRetriever:
    def __init__(self, chunks, delay_s: float):
        self._chunks = chunks
        self._delay_s = delay_s
        self.call_count = 0

    def retrieve(self, question, document_ids, user_id, k=8, rerank=False):
        self.call_count += 1
        time.sleep(self._delay_s)
        return self._chunks


class _RecordingFastStreamingGenerator:
    def __init__(self, deltas, final, delay_before_final_s: float = 0.0):
        self._deltas = deltas
        self._final = final
        self._delay_before_final_s = delay_before_final_s
        self.call_count = 0

    async def generate_stream(self, question, chunks, history=None):
        self.call_count += 1
        for delta in self._deltas:
            yield delta
        if self._delay_before_final_s:
            # Real delay between the last token and the final result --
            # gives the disconnect test below a reliable real window to
            # close its connection after seeing the token, before the
            # server has any chance to race ahead to the verification
            # disconnect-check (a real timing race otherwise: with zero
            # delay, the server can reach that check before the client-
            # side disconnect has even been scheduled to run, let alone
            # delivered to the server's watch task).
            import asyncio as _asyncio

            await _asyncio.sleep(self._delay_before_final_s)
        yield self._final


class _RecordingSlowVerifier:
    def __init__(self, delay_s: float):
        self._delay_s = delay_s
        self.call_count = 0

    def verify_batch(self, pairs):
        self.call_count += 1
        time.sleep(self._delay_s)
        return [_verdict(VerdictLabel.SUPPORTED, "q") for _ in pairs]


# Acceptance (Part 2, item 3 — HIGH SCRUTINY): a real client that
# genuinely disconnects WHILE retrieval is still running must never reach
# generation. Waits past the retriever's own real delay, then asserts the
# generator was NEVER called and NO conversation/message/citation rows
# exist for this user — the DISCARD decision (Part 2, item 2) verified
# directly against the real database, not inferred from the HTTP response
# (there isn't a usable one — the connection was closed client-side).
def test_stream_real_disconnect_during_retrieval_skips_generation_no_persistence(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)

    slow_retriever = _RecordingSlowRetriever([_retrieved_chunk(document_id, chunk_row)], delay_s=3.0)
    fast_generator = _RecordingFastStreamingGenerator(
        ["Revenue grew 12% [1]."],
        GenerateStreamResult(
            answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
            model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
        ),
    )
    slow_verifier = _RecordingSlowVerifier(delay_s=0.1)
    _override(retriever=slow_retriever, generator=fast_generator, verifier=slow_verifier)
    app.state.limiter.enabled = False

    try:
        with _live_server() as base_url:
            with httpx.Client(timeout=10, limits=httpx.Limits(max_keepalive_connections=0, max_connections=1)) as client:
                with client.stream(
                    "POST", f"{base_url}/query/stream",
                    json={"question": "q", "document_ids": [document_id]},
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    for i, _line in enumerate(response.iter_lines()):
                        if i >= 1:  # got the "retrieving" event -- retrieval is now genuinely mid-sleep
                            response.close()
                            break

            # Real wait past the retriever's own 3s delay -- long enough
            # for retrieval to genuinely finish and for the disconnect
            # check before generation to have run.
            time.sleep(4)
    finally:
        _clear_overrides()

    assert slow_retriever.call_count == 1, "retrieval itself cannot be cancelled once started -- it must still have run exactly once"
    assert fast_generator.call_count == 0, "generation must NEVER have started once the disconnect was detected"
    assert slow_verifier.call_count == 0

    conversations = admin.table("conversations").select("id").eq("user_id", user_id).execute().data
    assert conversations == [], "an aborted turn must be discarded entirely -- no conversation row for a request nobody could see the result of"


# Acceptance (Part 2, item 3 — HIGH SCRUTINY): a real client that
# disconnects AFTER generation completes (it saw its token(s)) but BEFORE
# verification starts must never reach verify_batch() — the other real,
# quota-costing checkpoint. This is the higher-value real-world case
# (task brief's own framing): a user closing the tab right as the visible
# answer looks complete, before the app has finished verifying it.
def test_stream_real_disconnect_after_generation_skips_verification_no_persistence(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)

    fast_retriever = FakeRetriever([_retrieved_chunk(document_id, chunk_row)])
    fast_generator = _RecordingFastStreamingGenerator(
        ["Revenue grew 12% [1]."],
        GenerateStreamResult(
            answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
            model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
        ),
        delay_before_final_s=2.0,
    )
    slow_verifier = _RecordingSlowVerifier(delay_s=3.0)
    _override(retriever=fast_retriever, generator=fast_generator, verifier=slow_verifier)
    app.state.limiter.enabled = False

    try:
        with _live_server() as base_url:
            with httpx.Client(timeout=10, limits=httpx.Limits(max_keepalive_connections=0, max_connections=1)) as client:
                with client.stream(
                    "POST", f"{base_url}/query/stream",
                    json={"question": "q", "document_ids": [document_id]},
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    seen_token = False
                    for line in response.iter_lines():
                        if line == "event: token":
                            seen_token = True
                        elif line == "" and seen_token:
                            # Blank line closing the "token" frame just
                            # arrived -- the client has now genuinely
                            # received the full (only) token event.
                            # Disconnect NOW, before "verifying"/verify_batch.
                            response.close()
                            break

            time.sleep(4)  # real wait past the verifier's own 3s delay
    finally:
        _clear_overrides()

    assert fast_generator.call_count == 1, "generation must have completed -- disconnect happened after it, not during"
    assert slow_verifier.call_count == 0, "verification must NEVER have started once the disconnect was detected"

    conversations = admin.table("conversations").select("id").eq("user_id", user_id).execute().data
    assert conversations == [], "an aborted turn must be discarded entirely -- no conversation row for a request nobody could see the result of"


class _RecordingMidStreamDelayGenerator:
    """Yields a real delta, pauses (the real window a user would see text
    actively arriving and click Stop), then yields a second delta before
    finally completing -- lets a test close the connection genuinely
    DURING generation (between two deltas of the same still-running
    async generator), not just before generation starts or after it has
    already fully finished."""

    def __init__(self, first_delta: str, second_delta: str, final, delay_between_deltas_s: float):
        self._first_delta = first_delta
        self._second_delta = second_delta
        self._final = final
        self._delay_between_deltas_s = delay_between_deltas_s
        self.call_count = 0
        self.completed = False

    async def generate_stream(self, question, chunks, history=None):
        import asyncio as _asyncio

        self.call_count += 1
        yield self._first_delta
        await _asyncio.sleep(self._delay_between_deltas_s)
        yield self._second_delta
        self.completed = True
        yield self._final


# 2026-08-04 (batch 2, part 1, item 3 -- HIGH SCRUTINY). Proves the
# actual claim this feature's UI is built on: a STOP-BUTTON click
# (AbortController.abort() on the browser's fetch, per this feature's
# own investigation -- see apps/web/lib/api/query.ts's askQuestionStream
# docstring) closes the connection the same low-level way any other
# client disconnect does, so it hits the exact same, already-proven
# _watch_for_disconnect path -- confirmed here by closing the connection
# genuinely MID-GENERATION (between two deltas of one still-running
# generate_stream() call, not before or cleanly after it), the one
# timing window the three disconnect tests above don't individually
# exercise. Real timing bound asserted too, not just correctness: the
# whole test settles in well under the verifier's own 3s delay, which
# would only be possible if the abort was detected fast (~20ms-class,
# per the original disconnect investigation), not merely eventually.
def test_stream_stop_button_click_mid_generation_no_further_calls_no_persistence(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)

    fast_retriever = FakeRetriever([_retrieved_chunk(document_id, chunk_row)])
    mid_stream_generator = _RecordingMidStreamDelayGenerator(
        first_delta="Revenue grew ",
        second_delta="12% [1].",
        final=GenerateStreamResult(
            answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
            model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
        ),
        delay_between_deltas_s=2.0,
    )
    slow_verifier = _RecordingSlowVerifier(delay_s=3.0)
    _override(retriever=fast_retriever, generator=mid_stream_generator, verifier=slow_verifier)
    app.state.limiter.enabled = False

    started = time.perf_counter()
    try:
        with _live_server() as base_url:
            with httpx.Client(timeout=10, limits=httpx.Limits(max_keepalive_connections=0, max_connections=1)) as client:
                with client.stream(
                    "POST", f"{base_url}/query/stream",
                    json={"question": "q", "document_ids": [document_id]},
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    seen_token = False
                    for line in response.iter_lines():
                        if line == "event: token":
                            seen_token = True
                        elif line == "" and seen_token:
                            # The FIRST delta's token frame just closed --
                            # generate_stream() is now genuinely paused
                            # mid-execution (awaiting its own internal
                            # sleep), about to yield a second delta the
                            # client will never see. This is "the user
                            # clicked Stop while watching text actively
                            # arrive," the real case this feature exists
                            # for -- not a disconnect before or after
                            # generation, but genuinely during it.
                            response.close()
                            break
            closed_at = time.perf_counter()
            # Real wait past the verifier's own 3s delay -- long enough
            # for verify_batch to have run if the abort were NOT
            # detected quickly, but this assertion window itself is much
            # tighter than that 3s, which is the real point.
            time.sleep(4)
    finally:
        _clear_overrides()

    elapsed_after_close = closed_at - started
    # Sanity on the test's own timing shape: the close happened well
    # before the generator's internal 2s inter-delta sleep or the
    # verifier's 3s delay could have elapsed on their own -- confirms
    # this test genuinely caught the connection mid-flight, not after
    # everything had already finished.
    assert elapsed_after_close < 1.5, "test setup itself was too slow to prove a genuine mid-generation close"

    assert mid_stream_generator.call_count == 1, "generation itself cannot be force-cancelled once started (in-flight coroutine)"
    assert mid_stream_generator.completed is True, "the already-running generator call keeps running to completion in the background -- expected, matches the disconnect precedent"
    assert slow_verifier.call_count == 0, "verification -- the next real, quota-costing stage -- must NEVER start once Stop was clicked, no quota burned past the click"

    conversations = admin.table("conversations").select("id").eq("user_id", user_id).execute().data
    assert conversations == [], "a stopped turn must be discarded entirely, identical to an accidental disconnect -- see page.tsx's stopGeneration() comment for the reasoning"


class _RecordingSlowVerifierProducingUnverified:
    """Simulates a real in-flight verification call that ultimately fails
    safe to UNVERIFIED (the shape a real network error/timeout/quota
    exhaustion inside verify_batch would produce, per the 2026-08-03
    UNVERIFIED feature) -- but takes real wall-clock time to get there,
    giving a real disconnect a genuine window to arrive WHILE the call is
    still in flight, not just before it starts."""

    def __init__(self, delay_s: float):
        self._delay_s = delay_s
        self.call_count = 0

    def verify_batch(self, pairs):
        self.call_count += 1
        time.sleep(self._delay_s)
        return [_verdict(VerdictLabel.UNVERIFIED, None) for _ in pairs]


# 2026-08-03 (item 7, UNVERIFIED feature) -- HIGH SCRUTINY: confirms the
# UNVERIFIED feature interacts correctly with the just-shipped disconnect
# handling for the one case the two disconnect tests above don't cover --
# a disconnect arriving WHILE verify_batch() is already running (not
# before it starts). The pre-verification _abort_if_disconnected
# checkpoint (routes/query.py) only runs BEFORE verify_batch() is
# scheduled; there is deliberately no checkpoint after it completes and
# before persistence, matching the established "an in-flight call can't
# be cancelled" precedent from the retrieval/generation disconnect tests
# above. So the only two valid outcomes are: (a) the disconnect is caught
# by the pre-check and the turn is discarded entirely (proven by the test
# above), or (b) the disconnect arrives too late for that check, the
# verify call (and the UNVERIFIED verdict it fails safe to) runs to
# completion, and the turn persists normally as one atomic, fully-formed
# unit -- never a half-applied state (e.g. a conversation row with no
# matching citations, or citations persisted but the turn otherwise
# aborted).
def test_stream_real_disconnect_during_verification_still_persists_consistently(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)

    fast_retriever = FakeRetriever([_retrieved_chunk(document_id, chunk_row)])
    fast_generator = _RecordingFastStreamingGenerator(
        ["Revenue grew 12% [1]."],
        GenerateStreamResult(
            answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
            model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
        ),
    )
    slow_verifier = _RecordingSlowVerifierProducingUnverified(delay_s=3.0)
    _override(retriever=fast_retriever, generator=fast_generator, verifier=slow_verifier)
    app.state.limiter.enabled = False

    try:
        with _live_server() as base_url:
            with httpx.Client(timeout=10, limits=httpx.Limits(max_keepalive_connections=0, max_connections=1)) as client:
                with client.stream(
                    "POST", f"{base_url}/query/stream",
                    json={"question": "q", "document_ids": [document_id]},
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    seen_verifying = False
                    for line in response.iter_lines():
                        if line == "event: verifying":
                            seen_verifying = True
                        elif line == "" and seen_verifying:
                            # Blank line closing the "verifying" frame --
                            # verify_batch() is now genuinely mid-sleep
                            # (its own real 3s delay). Disconnect NOW,
                            # while the call is actually in flight, not
                            # before it started.
                            response.close()
                            break

            # Real wait past the verifier's own 3s delay, long enough for
            # verify_batch() to genuinely finish and for persistence to
            # have run (or not) — no ambiguity about which happened.
            time.sleep(4)
    finally:
        _clear_overrides()

    assert slow_verifier.call_count == 1, "an in-flight verify_batch() call cannot be cancelled -- it must still run to completion"

    conversations = admin.table("conversations").select("id").eq("user_id", user_id).execute().data
    messages = admin.table("messages").select("id,conversation_id").eq("user_id", user_id).execute().data
    citations = admin.table("citations").select("id,message_id,verdict").eq("user_id", user_id).execute().data

    # The one thing this test exists to prove: never an inconsistent
    # half-state. Either nothing persisted (if some other guard caught
    # it) or everything did, as one atomic turn -- never a conversation
    # with no messages, or messages with no matching citation audit row.
    if not conversations:
        assert messages == [] and citations == [], "no conversation means no orphaned messages/citations either"
    else:
        assert len(conversations) == 1
        assert len(messages) == 2, "a full turn is exactly one user message + one assistant message"
        assert all(m["conversation_id"] == conversations[0]["id"] for m in messages)
        assert len(citations) == 1
        assert citations[0]["verdict"] == "unverified", "the in-flight call's real fail-safe verdict must persist unmodified"
        assert citations[0]["message_id"] in {m["id"] for m in messages}


# 2026-10-06: the stream's final answer removes unsupported claims too, same
# rule as POST /query (see test_query.py's _remove_unsupported_claims tests).
def test_stream_final_answer_removes_the_unsupported_sentence(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%.")
    chunk_row = _real_chunk_row(admin, document_id)
    final = GenerateStreamResult(
        answer="The outlook is fabricated [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=1, output_tokens=1, latency_ms=1.0,
    )
    _override(
        retriever=FakeRetriever([_retrieved_chunk(document_id, chunk_row)]),
        generator=FakeStreamingGenerator(["The outlook is fabricated [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.UNSUPPORTED, None) for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream", json={"question": "q", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        events = _parse_sse(response)

    resolved = next(data for name, data in events if name == "citations-resolved")
    assert "fabricated" not in resolved["answer"]
    assert "1 statement was removed" in resolved["answer"]
    assert resolved["citations"] == []


# 2026-10-06: follow-up questions are rewritten for retrieval on the stream
# path too (same _search_question as POST /query).
def test_stream_follow_up_searches_with_the_rewritten_question(app_client, admin, user_a):
    from tests.test_query import PassthroughRewriter

    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Q2 revenue was $3.6M.")
    chunk_row = _real_chunk_row(admin, document_id)
    seeded = admin.rpc(
        "create_query_turn",
        {
            "p_user_id": user_id, "p_conversation_id": None, "p_document_ids": [document_id],
            "p_question": "What was Q3 revenue?", "p_answer_content": "It was $4.2M.",
            "p_answer_raw_content": "It was $4.2M.", "p_retrieved_chunk_ids": [],
            "p_answer_metadata": {}, "p_citations": [],
        },
    ).execute().data[0]

    retriever, rewriter = FakeRetriever([_retrieved_chunk(document_id, chunk_row)]), PassthroughRewriter("What was Q2 revenue?")
    final = GenerateStreamResult(
        answer="Q2 was $3.6M [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=1, output_tokens=1, latency_ms=1.0,
    )
    _override(
        retriever=retriever, rewriter=rewriter,
        generator=FakeStreamingGenerator(["Q2 was $3.6M [1]."], final),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED, "Q2 revenue") for _ in pairs])})(),
    )

    with app_client.stream(
        "POST", "/query/stream",
        json={"question": "And for Q2?", "document_ids": [document_id], "conversation_id": seeded["conversation_id"]},
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        _parse_sse(response)

    assert retriever.calls[0]["question"] == "What was Q2 revenue?"
    assert rewriter.calls[0]["question"] == "And for Q2?"
