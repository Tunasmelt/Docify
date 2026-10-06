# Regression tests for event-loop blocking (2026-10-06).
#
# Every route used to be `async def` while calling the synchronous Supabase
# client (and, in POST /query, the synchronous retriever/Gemini/verifier)
# directly. An `async def` handler runs ON the event loop, so one /query
# froze the whole single-worker server for its full 5-10s: other users'
# requests, /health, and in-flight SSE streams (including their keepalives)
# all stalled. Blocking handlers are now plain `def`, which FastAPI runs in
# its threadpool; the one async route (/query/stream) pushes every blocking
# call through asyncio.to_thread.

import asyncio
import threading
import time

import httpx
from fastapi.routing import APIRoute

from main import app
from services.generator import GenerateResult
from services.verifier import VerdictLabel
from tests.test_query import FakeGenerator, FakeVerifier, _override, _setup_single_chunk_query, _verdict
from tests.test_query_stream import _live_server

# Routes allowed to be `async def`: they must not make blocking calls on the
# event loop (health does no I/O; query/stream awaits asyncio.to_thread).
_ASYNC_ALLOWED = {"/health", "/query/stream"}


def test_blocking_route_handlers_are_sync_so_they_run_in_the_threadpool():
    offenders = [
        f"{sorted(route.methods)} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path not in _ASYNC_ALLOWED
        and asyncio.iscoroutinefunction(route.endpoint)
    ]
    assert offenders == [], (
        "these handlers are `async def` but call blocking Supabase/vendor clients — "
        f"make them plain `def` or move every blocking call to asyncio.to_thread: {offenders}"
    )


class _SlowRetriever:
    """Blocks with time.sleep, exactly like the real synchronous retriever."""

    def __init__(self, chunks, delay_s: float):
        self._chunks = chunks
        self._delay_s = delay_s

    def retrieve(self, question, document_ids, user_id, k=8, rerank=False):
        time.sleep(self._delay_s)
        return self._chunks


def test_a_slow_query_does_not_block_other_requests(app_client, admin, user_a):
    user_id, token = user_a
    document_id, chunk_row, retrieved = _setup_single_chunk_query(app_client, admin, user_id, token)
    _override(
        retriever=_SlowRetriever(retrieved, delay_s=2.0),
        generator=FakeGenerator(
            GenerateResult(
                answer="A fact [1].", cited_indices=[1], hallucinated_markers=[],
                model="gemini-3.6-flash", input_tokens=1, output_tokens=1, latency_ms=1.0,
            )
        ),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
    )

    with _live_server() as base_url:
        query_result: dict = {}

        def run_query():
            response = httpx.post(
                f"{base_url}/query",
                json={"question": "q", "document_ids": [document_id]},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            query_result["status"] = response.status_code

        query_thread = threading.Thread(target=run_query)
        query_thread.start()
        time.sleep(0.5)  # let /query reach the 2s blocking retrieve()

        started = time.perf_counter()
        health = httpx.get(f"{base_url}/health", timeout=10)
        health_latency = time.perf_counter() - started
        query_still_running = query_thread.is_alive()

        query_thread.join(timeout=30)

    assert health.status_code == 200
    assert query_still_running, "test setup: /query finished before /health was measured"
    assert health_latency < 0.5, f"/health waited {health_latency:.2f}s behind a blocking /query"
    assert query_result["status"] == 200
