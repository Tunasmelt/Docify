"""Cohere fallback (services/cohere_client.py and its callers). No network:
HTTP is replaced with canned responses, and the Gemini/Voyage clients are fakes
that fail the way a real outage does."""

import asyncio
import json

import httpx
import pytest
from google.genai.errors import ClientError, ServerError
from voyageai.error import RateLimitError

from services import cohere_client
from services.chunker import Chunk
from services.embedder import EmbedError, Embedder
from services.generator import GenerationError, Generator, GeneratorChunk
from services.parser import ElementType
from services.query_rewriter import QueryRewriter
from services.retriever import Retriever
from services.verifier import Verifier, VerdictLabel


@pytest.fixture(autouse=True)
def cohere_key(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")


def _response(status=200, body=None, headers=None):
    return httpx.Response(status, json=body if body is not None else {}, headers=headers, request=httpx.Request("POST", "https://x"))


def _chat_reply(text, in_tokens=11, out_tokens=7):
    return {"message": {"content": [{"type": "text", "text": text}]}, "usage": {"billed_units": {"input_tokens": in_tokens, "output_tokens": out_tokens}}}


def _no_sleep(_seconds):
    return None


# --- cohere_client -----------------------------------------------------------


def test_chat_returns_text_and_usage(monkeypatch):
    sent = {}

    def fake_post(url, json, headers, timeout):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return _response(200, _chat_reply("hello"))

    monkeypatch.setattr(cohere_client.httpx, "post", fake_post)
    result = cohere_client.chat("sys", "user text", json_schema={"type": "object"})

    assert (result.text, result.input_tokens, result.output_tokens) == ("hello", 11, 7)
    assert sent["url"].endswith("/chat") and sent["auth"] == "Bearer test-key"
    assert sent["body"]["response_format"] == {"type": "json_object", "json_schema": {"type": "object"}}


def test_chat_retries_a_rate_limit_then_succeeds(monkeypatch):
    replies = iter([_response(429, {"message": "slow down"}, {"retry-after": "2"}), _response(200, _chat_reply("ok"))])
    monkeypatch.setattr(cohere_client.httpx, "post", lambda *a, **k: next(replies))
    waits = []

    assert cohere_client.chat("s", "u", sleep_fn=waits.append).text == "ok"
    assert waits == [2.0]


def test_chat_gives_up_at_once_when_the_server_asks_for_a_long_wait(monkeypatch):
    monkeypatch.setattr(cohere_client.httpx, "post", lambda *a, **k: _response(429, {}, {"retry-after": "60"}))

    with pytest.raises(cohere_client.CohereTransientError):
        cohere_client.chat("s", "u", sleep_fn=_no_sleep)


def test_chat_does_not_retry_a_bad_key(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return _response(401, {"message": "invalid api token"})

    monkeypatch.setattr(cohere_client.httpx, "post", fake_post)

    with pytest.raises(cohere_client.CohereError) as excinfo:
        cohere_client.chat("s", "u", sleep_fn=_no_sleep)
    assert not isinstance(excinfo.value, cohere_client.CohereTransientError)
    assert len(calls) == 1


_STREAM_EVENTS = [
    '{"type":"content-delta","delta":{"message":{"content":{"text":"Rev"}}}}',
    '{"type":"content-delta","delta":{"message":{"content":{"text":"enue [1]."}}}}',
    '{"type":"message-end","delta":{"usage":{"billed_units":{"input_tokens":5,"output_tokens":3}}}}',
]
_SSE_BODY = "\n".join(f"event: x\ndata: {event}\n" for event in _STREAM_EVENTS) + "\ndata: [DONE]\n"
_JSON_LINES_BODY = "\n".join(_STREAM_EVENTS) + "\n"


@pytest.mark.parametrize("sse", [_SSE_BODY, _JSON_LINES_BODY], ids=["sse", "json-lines"])
def test_chat_stream_yields_deltas_then_the_final_result(monkeypatch, sse):
    real = httpx.AsyncClient
    handler = lambda request: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})  # noqa: E731
    monkeypatch.setattr(cohere_client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def collect():
        return [item async for item in cohere_client.chat_stream("s", "u")]

    items = asyncio.run(collect())

    assert items[:2] == ["Rev", "enue [1]."]
    assert (items[2].text, items[2].input_tokens, items[2].output_tokens) == ("Revenue [1].", 5, 3)


def test_embed_batches_and_encodes_images(monkeypatch):
    bodies = []

    def fake_post(url, json, headers, timeout):
        bodies.append(json)
        return _response(200, {"embeddings": {"float": [[0.5] * 1024 for _ in json["inputs"]]}})

    monkeypatch.setattr(cohere_client.httpx, "post", fake_post)
    items = [["text"]] * 100 + [["caption", b"\x89PNG"]]
    vectors = cohere_client.embed(items, "search_document")

    assert len(vectors) == 101
    assert [len(b["inputs"]) for b in bodies] == [96, 5]
    image_segment = bodies[1]["inputs"][-1]["content"][1]
    assert image_segment["type"] == "image_url" and image_segment["image_url"]["url"].startswith("data:image/png;base64,")
    assert bodies[0]["output_dimension"] == 1024 and bodies[0]["input_type"] == "search_document"


def test_embed_rejects_a_misaligned_response(monkeypatch):
    monkeypatch.setattr(cohere_client.httpx, "post", lambda *a, **k: _response(200, {"embeddings": {"float": [[0.1]]}}))

    with pytest.raises(cohere_client.CohereError):
        cohere_client.embed([["a"], ["b"]], "search_query")


# --- generator ---------------------------------------------------------------


def _gemini_error(code):
    error_class, status = (ServerError, "UNAVAILABLE") if code >= 500 else (ClientError, "INVALID_ARGUMENT")
    return error_class(code, httpx.Response(code, json={"error": {"message": "boom", "status": status}}))


class _GeminiDown:
    def __init__(self, code=503):
        self.calls = 0
        self.models = self
        self._code = code

    def generate_content(self, **kwargs):
        self.calls += 1
        raise _gemini_error(self._code)


def _gen_chunks():
    return [GeneratorChunk(chunk_id="c1", content="Revenue was 4.2M.", element_type="text", page_number=1, document_name="a.pdf")]


def test_generate_falls_back_to_cohere_when_gemini_is_unavailable(monkeypatch):
    seen = {}

    def fake_chat(system, user_text, **kwargs):
        seen["user_text"] = user_text
        return cohere_client.ChatResult(text="Revenue was 4.2M [1].", model="command-a-03-2025", input_tokens=9, output_tokens=4)

    monkeypatch.setattr(cohere_client, "chat", fake_chat)
    result = Generator(client=_GeminiDown(), retry_sleep=_no_sleep).generate("What was revenue?", _gen_chunks())

    assert result.answer == "Revenue was 4.2M [1]." and result.cited_indices == [1]
    assert result.model == "command-a-03-2025"
    assert "[1] (page 1, text, from a.pdf)" in seen["user_text"] and "Question: What was revenue?" in seen["user_text"]


def test_generate_does_not_use_cohere_for_a_non_transient_gemini_error(monkeypatch):
    monkeypatch.setattr(cohere_client, "chat", lambda *a, **k: pytest.fail("Cohere must not be called"))

    with pytest.raises(GenerationError):
        Generator(client=_GeminiDown(code=400), retry_sleep=_no_sleep).generate("q", _gen_chunks())


def test_generate_keeps_failing_when_no_cohere_key(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY")

    with pytest.raises(GenerationError):
        Generator(client=_GeminiDown(), retry_sleep=_no_sleep).generate("q", _gen_chunks())


def test_generate_reports_both_failures_when_cohere_also_fails(monkeypatch):
    def failing_chat(*args, **kwargs):
        raise cohere_client.CohereTransientError("cohere down")

    monkeypatch.setattr(cohere_client, "chat", failing_chat)

    with pytest.raises(GenerationError, match="Cohere fallback also failed"):
        Generator(client=_GeminiDown(), retry_sleep=_no_sleep).generate("q", _gen_chunks())


class _GeminiStreamDown:
    def __init__(self):
        self.aio = self
        self.models = self

    async def generate_content_stream(self, **kwargs):
        raise _gemini_error(503)


def test_generate_stream_falls_back_to_cohere_before_any_text_is_sent(monkeypatch):
    async def fake_stream(system, user_text, **kwargs):
        yield "Revenue "
        yield "was 4.2M [1]."
        yield cohere_client.ChatResult(text="Revenue was 4.2M [1].", model="command-a-03-2025", input_tokens=9, output_tokens=4)

    monkeypatch.setattr(cohere_client, "chat_stream", fake_stream)
    generator = Generator(client=_GeminiStreamDown(), async_retry_sleep=lambda s: asyncio.sleep(0))

    async def collect():
        return [item async for item in generator.generate_stream("What was revenue?", _gen_chunks())]

    items = asyncio.run(collect())

    assert items[:2] == ["Revenue ", "was 4.2M [1]."]
    assert items[2].answer == "Revenue was 4.2M [1]." and items[2].cited_indices == [1] and items[2].model == "command-a-03-2025"


# --- verifier ----------------------------------------------------------------


def test_verify_falls_back_to_cohere_and_still_checks_the_quote(monkeypatch):
    chunk = _gen_chunks()[0]
    replies = iter(['{"verdict": "supported", "quote": "Revenue was 4.2M."}', '{"verdict": "supported", "quote": "not in the source"}'])
    monkeypatch.setattr(
        cohere_client, "chat", lambda *a, **k: cohere_client.ChatResult(text=next(replies), model="command-a-03-2025", input_tokens=1, output_tokens=1)
    )
    verifier = Verifier(client=_GeminiDown(), retry_sleep=_no_sleep)

    good = verifier.verify("Revenue was 4.2M.", chunk)
    fabricated = verifier.verify("Revenue was 4.2M.", chunk)

    assert (good.verdict, good.quote, good.model) == (VerdictLabel.SUPPORTED, "Revenue was 4.2M.", "command-a-03-2025")
    assert fabricated.verdict == VerdictLabel.UNSUPPORTED  # a fabricated quote is rejected whichever model said it


def test_verify_stays_unverified_when_cohere_is_not_configured(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY")

    verdict = Verifier(client=_GeminiDown(), retry_sleep=_no_sleep).verify("Revenue was 4.2M.", _gen_chunks()[0])

    assert verdict.verdict == VerdictLabel.UNVERIFIED


def test_verify_batch_uses_one_cohere_call_for_the_group(monkeypatch):
    calls = []

    def fake_chat(system, user_text, **kwargs):
        calls.append(kwargs["json_schema"])
        return cohere_client.ChatResult(
            text=json.dumps({"results": [{"item": 1, "verdict": "supported", "quote": "Revenue was 4.2M."}, {"item": 2, "verdict": "unsupported"}]}),
            model="command-a-03-2025",
            input_tokens=10,
            output_tokens=4,
        )

    monkeypatch.setattr(cohere_client, "chat", fake_chat)
    chunk = _gen_chunks()[0]
    verdicts = Verifier(client=_GeminiDown(), retry_sleep=_no_sleep).verify_batch([("Revenue was 4.2M.", chunk), ("Profit doubled.", chunk)])

    assert [v.verdict for v in verdicts] == [VerdictLabel.SUPPORTED, VerdictLabel.UNSUPPORTED]
    assert len(calls) == 1


# --- query rewriter ----------------------------------------------------------


def test_rewriter_falls_back_to_cohere(monkeypatch):
    monkeypatch.setattr(
        cohere_client, "chat", lambda *a, **k: cohere_client.ChatResult(text='"Q2 revenue"', model="m", input_tokens=1, output_tokens=1)
    )
    history = [{"role": "user", "content": "What was Q3 revenue?"}, {"role": "assistant", "content": "4.2M"}]

    assert QueryRewriter(client=_GeminiDown(), retry_sleep=_no_sleep).rewrite("and for Q2?", history) == "Q2 revenue"


# --- embeddings --------------------------------------------------------------


class _FakeTokenizer:
    def encode(self, text):
        return type("Enc", (), {"ids": list(range(len(text) // 4))})()


class _VoyageDown:
    def multimodal_embed(self, **kwargs):
        raise RateLimitError("simulated 429, never recovers")


class _GeminiEmbedDown:
    def __init__(self):
        self.models = self

    def embed_content(self, **kwargs):
        raise RuntimeError("simulated Gemini failure")


def _embed_chunk(index=0, content="some text"):
    return Chunk(chunk_index=index, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content=content, image=None)


def test_embed_falls_through_voyage_and_gemini_to_cohere(monkeypatch):
    seen = {}

    def fake_embed(items, input_type, **kwargs):
        seen.update(items=items, input_type=input_type)
        return [[0.25] * 1024 for _ in items]

    monkeypatch.setattr(cohere_client, "embed", fake_embed)
    embedder = Embedder(client=_VoyageDown(), tokenizer=_FakeTokenizer(), gemini_client=_GeminiEmbedDown(), sleep_fn=_no_sleep)

    embedded = embedder.embed([_embed_chunk(0, "alpha"), _embed_chunk(1, "beta")])

    assert [e.provider for e in embedded] == ["cohere", "cohere"]
    assert seen["items"] == [["alpha"], ["beta"]] and seen["input_type"] == "search_document"


def test_embed_error_names_every_provider_when_cohere_is_unconfigured(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY")
    embedder = Embedder(client=_VoyageDown(), tokenizer=_FakeTokenizer(), gemini_client=_GeminiEmbedDown(), sleep_fn=_no_sleep)

    with pytest.raises(EmbedError, match="all providers exhausted"):
        embedder.embed([_embed_chunk()])


def test_embed_query_cohere_uses_the_query_input_type(monkeypatch):
    seen = {}
    monkeypatch.setattr(cohere_client, "embed", lambda items, input_type, **k: seen.update(input_type=input_type) or [[0.1] * 1024])

    assert len(Embedder().embed_query("hello", provider="cohere")) == 1024
    assert seen["input_type"] == "search_query"


# --- retriever ---------------------------------------------------------------


def test_retriever_skips_a_provider_whose_query_embedding_fails():
    class FailingEmbedder:
        def embed_query(self, text, *, provider):
            raise EmbedError("voyage is down")

    retriever = Retriever(client=object(), embedder=FailingEmbedder())

    assert retriever._embed_and_search_for_provider("q", ["d1"], "u1", 10, "voyage") == []


def test_chat_stream_rejects_truncated_response(monkeypatch):
    real = httpx.AsyncClient
    body = "\n".join(_STREAM_EVENTS[:2]) + "\n"
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=body)
    monkeypatch.setattr(cohere_client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    async def collect():
        return [item async for item in cohere_client.chat_stream("s", "u")]
    with pytest.raises(cohere_client.CohereError, match="message-end"):
        asyncio.run(collect())
    assert len(calls) == 1  # Never replay text already delivered.


@pytest.mark.parametrize("vector", [[0.1], [float("nan")] * 1024])
def test_embed_rejects_invalid_vectors(monkeypatch, vector):
    monkeypatch.setattr(cohere_client, "_post", lambda *a: {"embeddings": {"float": [vector]}})
    with pytest.raises(cohere_client.CohereError, match="invalid embedding"):
        cohere_client.embed([["a"]], "search_query")
