# Bounded retries for interactive Gemini calls (2026-10-06).
#
# One transient 429/503 used to fail the whole request: generation returned
# GENERATE_FAILED and verification marked citations `unverified`. Generation
# and verification now retry transient failures up to 3 attempts in total,
# honoring the server's retryDelay — but never waiting more than
# MAX_DELAY_SECONDS on an interactive request.

import asyncio

import httpx
import pytest
from google.genai.errors import ClientError, ServerError

from services import gemini_retry
from services.generator import GenerationError, Generator
from services.verifier import Verifier, VerdictLabel, _VerdictResponse
from tests.test_embedder import _real_gemini_429_error
from tests.test_generator import FakeResponse, _chunk
from tests.test_verifier import FakeResponse as VerifierResponse


def _server_error(code: int = 503) -> ServerError:
    return ServerError(code, httpx.Response(code, json={"error": {"message": "unavailable", "status": "UNAVAILABLE"}}))


def _bad_request() -> ClientError:
    return ClientError(400, httpx.Response(400, json={"error": {"message": "bad", "status": "INVALID_ARGUMENT"}}))


class _ScriptedModels:
    """Raises the scripted exceptions in order, then returns `response`."""

    def __init__(self, failures, response):
        self._failures = list(failures)
        self._response = response
        self.calls = 0

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return self._response


class _Client:
    def __init__(self, models):
        self.models = models


def _generator(failures, sleeps):
    models = _ScriptedModels(failures, FakeResponse(text="An answer [1]."))
    return Generator(client=_Client(models), retry_sleep=sleeps.append), models


def test_generation_retries_a_transient_server_error_with_backoff():
    sleeps: list[float] = []
    generator, models = _generator([_server_error(503)], sleeps)

    result = generator.generate("q", [_chunk()])

    assert result.answer == "An answer [1]."
    assert models.calls == 2
    assert sleeps == [gemini_retry.BACKOFF_SECONDS[0]]


def test_generation_honors_a_short_server_retry_delay():
    sleeps: list[float] = []
    generator, models = _generator([_real_gemini_429_error("3s")], sleeps)

    generator.generate("q", [_chunk()])

    assert sleeps == [3.0]
    assert models.calls == 2


def test_generation_does_not_wait_out_a_long_server_retry_delay():
    sleeps: list[float] = []
    generator, models = _generator([_real_gemini_429_error("30s")], sleeps)

    with pytest.raises(GenerationError):
        generator.generate("q", [_chunk()])

    assert models.calls == 1
    assert sleeps == []


def test_generation_never_retries_a_bad_request():
    sleeps: list[float] = []
    generator, models = _generator([_bad_request()], sleeps)

    with pytest.raises(GenerationError):
        generator.generate("q", [_chunk()])

    assert models.calls == 1


def test_generation_gives_up_after_three_attempts():
    sleeps: list[float] = []
    generator, models = _generator([_server_error(), _server_error(), _server_error()], sleeps)

    with pytest.raises(GenerationError):
        generator.generate("q", [_chunk()])

    assert models.calls == 3
    assert len(sleeps) == 2


def test_generation_retries_a_network_timeout():
    sleeps: list[float] = []
    generator, models = _generator([httpx.ReadTimeout("timed out")], sleeps)

    assert generator.generate("q", [_chunk()]).answer == "An answer [1]."
    assert models.calls == 2


def test_verification_retries_a_transient_error_instead_of_marking_unverified():
    models = _ScriptedModels(
        [_server_error(503)],
        VerifierResponse(parsed=_VerdictResponse(verdict=VerdictLabel.SUPPORTED, quote="some content")),
    )
    verifier = Verifier(client=_Client(models), retry_sleep=lambda _s: None)

    result = verifier.verify("a claim", _chunk(content="some content"))

    assert result.verdict == VerdictLabel.SUPPORTED
    assert models.calls == 2


# --- streaming: retried only before the first token reaches the client -------


class _StreamChunk:
    def __init__(self, text):
        self.text = text
        self.model_version = "gemini-3.6-flash-001"
        self.usage_metadata = None
        self.prompt_feedback = None


class _Stream:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _AsyncModels:
    def __init__(self, scripted_streams):
        self._streams = list(scripted_streams)
        self.opens = 0

    async def generate_content_stream(self, *, model, contents, config):
        self.opens += 1
        stream = self._streams.pop(0)
        if isinstance(stream, Exception):
            raise stream
        return stream


class _StreamClient:
    def __init__(self, models):
        self.aio = type("Aio", (), {"models": models})()
        self.models = None


async def _collect(generator):
    return [item async for item in generator.generate_stream("q", [_chunk()])]


async def _no_sleep(_seconds):
    return None


def test_stream_retries_when_opening_the_stream_fails():
    models = _AsyncModels([_server_error(503), _Stream([_StreamChunk("Hello "), _StreamChunk("world [1].")])])
    generator = Generator(client=_StreamClient(models), async_retry_sleep=_no_sleep)

    items = asyncio.run(_collect(generator))

    assert items[:2] == ["Hello ", "world [1]."]
    assert items[-1].answer == "Hello world [1]."
    assert models.opens == 2


def test_stream_retries_when_the_first_chunk_fails():
    models = _AsyncModels([_Stream([_server_error(503)]), _Stream([_StreamChunk("Answer [1].")])])
    generator = Generator(client=_StreamClient(models), async_retry_sleep=_no_sleep)

    items = asyncio.run(_collect(generator))

    assert items[0] == "Answer [1]."
    assert models.opens == 2


def test_stream_is_not_retried_once_text_has_been_sent():
    models = _AsyncModels([_Stream([_StreamChunk("Partial "), _server_error(503)]), _Stream([_StreamChunk("never used")])])
    generator = Generator(client=_StreamClient(models), async_retry_sleep=_no_sleep)

    async def run():
        received = []
        with pytest.raises(GenerationError):
            async for item in generator.generate_stream("q", [_chunk()]):
                received.append(item)
        return received

    assert asyncio.run(run()) == ["Partial "]
    assert models.opens == 1
