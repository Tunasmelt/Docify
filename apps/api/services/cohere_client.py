"""Cohere as a last-resort fallback behind Gemini (text) and Voyage/Gemini
(embeddings). Plain httpx against Cohere's v2 REST API, so no SDK dependency.

Does nothing unless COHERE_API_KEY is set: callers check `is_configured()`
and keep their existing failure behaviour when it is not. Callers also fall
back only on `CohereTransientError`-class failures of the PRIMARY provider
(rate limits, 5xx, network), never on auth or invalid-request errors, so a
broken key or request still surfaces instead of hiding behind Cohere.

Cohere embeddings are a third, separate vector space (embed-v4.0 at 1024
dimensions, to fit chunks.embedding): never compared against Voyage or
Gemini vectors, see the embedding_provider notes in services/embedder.py.
"""

import asyncio
import base64
import json
import logging
import math
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.cohere.com/v2"
CHAT_MODEL = "command-a-03-2025"
EMBED_MODEL = "embed-v4.0"
EMBED_DIMENSION = 1024  # chunks.embedding is vector(1024); embed-v4.0 supports 256/512/1024/1536
EMBED_BATCH_SIZE = 96  # Cohere's per-request cap on embed inputs
TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)
MAX_DELAY_SECONDS = 8.0  # longest wait imposed on an interactive request

_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


class CohereError(Exception):
    """Any Cohere failure that retrying will not fix (bad key, bad request),
    or a response that doesn't have the expected shape."""


class CohereTransientError(CohereError):
    """Rate limit, 5xx or network failure that outlasted our bounded retries."""


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


def is_configured() -> bool:
    return bool(os.environ.get("COHERE_API_KEY", "").strip())


def _headers() -> dict[str, str]:
    key = os.environ.get("COHERE_API_KEY", "").strip()
    if not key:
        raise CohereError("COHERE_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}


def _retry_delay(attempt: int, retry_after: str | None) -> float | None:
    """Seconds to wait before the next attempt, or None to give up."""
    if attempt >= MAX_ATTEMPTS:
        return None
    delay = None
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = None
    if delay is None:
        delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
    return delay if delay <= MAX_DELAY_SECONDS else None


def _error_text(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or response.text)[:300]
    except Exception:
        return response.text[:300]


def _post(path: str, body: dict, sleep_fn: Callable[[float], None] = time.sleep) -> dict:
    attempt = 1
    while True:
        try:
            response = httpx.post(f"{API_URL}{path}", json=body, headers=_headers(), timeout=TIMEOUT_SECONDS)
        except httpx.TransportError as exc:
            delay = _retry_delay(attempt, None)
            if delay is None:
                raise CohereTransientError(f"Cohere {path} network failure: {exc}") from exc
        else:
            if response.status_code < 400:
                return response.json()
            if response.status_code not in _TRANSIENT_HTTP_CODES:
                raise CohereError(f"Cohere {path} returned {response.status_code}: {_error_text(response)}")
            delay = _retry_delay(attempt, response.headers.get("retry-after"))
            if delay is None:
                raise CohereTransientError(f"Cohere {path} returned {response.status_code}: {_error_text(response)}")
        logger.warning("cohere %s failed transiently (attempt %d/%d) — retrying in %.1fs", path, attempt, MAX_ATTEMPTS, delay)
        sleep_fn(delay)
        attempt += 1


def _chat_body(system: str, user_text: str, *, json_schema: dict | None, temperature: float, stream: bool) -> dict:
    body: dict = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
        "temperature": temperature,
    }
    if json_schema is not None:
        body["response_format"] = {"type": "json_object", "json_schema": json_schema}
    if stream:
        body["stream"] = True
    return body


def _billed(usage: dict | None) -> tuple[int, int]:
    billed = (usage or {}).get("billed_units") or {}
    return int(billed.get("input_tokens") or 0), int(billed.get("output_tokens") or 0)


def chat(
    system: str,
    user_text: str,
    *,
    json_schema: dict | None = None,
    temperature: float = 0.2,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ChatResult:
    """One non-streaming chat turn. With `json_schema` (an object schema), the
    reply text is JSON conforming to it."""
    started = time.perf_counter()
    data = _post("/chat", _chat_body(system, user_text, json_schema=json_schema, temperature=temperature, stream=False), sleep_fn)
    parts = (data.get("message") or {}).get("content") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
    if not text:
        raise CohereError("Cohere returned no text content")
    input_tokens, output_tokens = _billed(data.get("usage"))
    logger.info("cohere chat ok in %.0f ms", (time.perf_counter() - started) * 1000)
    return ChatResult(text=text, model=CHAT_MODEL, input_tokens=input_tokens, output_tokens=output_tokens)


async def chat_stream(
    system: str,
    user_text: str,
    *,
    temperature: float = 0.2,
    sleep_fn: Callable[[float], "asyncio.Future | None"] = asyncio.sleep,
) -> AsyncIterator[str | ChatResult]:
    """Yields each text delta as `str`, then one final ChatResult. Transient
    failures are retried only until the first byte of the stream is read."""
    body = _chat_body(system, user_text, json_schema=None, temperature=temperature, stream=True)
    attempt = 1
    yielded = False  # once text has reached the caller, a retry would duplicate it
    while True:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                async with client.stream("POST", f"{API_URL}/chat", json=body, headers=_headers()) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        if response.status_code not in _TRANSIENT_HTTP_CODES:
                            raise CohereError(f"Cohere /chat returned {response.status_code}: {_error_text(response)}")
                        delay = _retry_delay(attempt, response.headers.get("retry-after"))
                        if delay is None:
                            raise CohereTransientError(
                                f"Cohere /chat returned {response.status_code}: {_error_text(response)}"
                            )
                    else:
                        parts: list[str] = []
                        usage: dict | None = None
                        completed = False
                        async for line in response.aiter_lines():
                            # Cohere sends either SSE ("data: {...}") or bare
                            # JSON lines, depending on the Accept header.
                            raw = line[5:].strip() if line.startswith("data:") else line.strip()
                            if not raw or raw == "[DONE]" or line.startswith("event:"):
                                continue
                            try:
                                event = json.loads(raw)
                            except ValueError as exc:
                                raise CohereError("Cohere returned an invalid stream event") from exc
                            if event.get("type") == "content-delta":
                                text = (((event.get("delta") or {}).get("message") or {}).get("content") or {}).get("text")
                                if text:
                                    parts.append(text)
                                    yielded = True
                                    yield text
                            elif event.get("type") == "message-end":
                                completed = True
                                usage = (event.get("delta") or {}).get("usage")
                        if not completed:
                            raise CohereError("Cohere stream ended before message-end")
                        if not parts:
                            raise CohereError("Cohere returned no text content")
                        input_tokens, output_tokens = _billed(usage)
                        yield ChatResult(text="".join(parts), model=CHAT_MODEL, input_tokens=input_tokens, output_tokens=output_tokens)
                        return
        except httpx.TransportError as exc:
            delay = None if yielded else _retry_delay(attempt, None)
            if delay is None:
                raise CohereTransientError(f"Cohere /chat network failure: {exc}") from exc
        logger.warning("cohere /chat stream failed transiently (attempt %d/%d) — retrying in %.1fs", attempt, MAX_ATTEMPTS, delay)
        await sleep_fn(delay)
        attempt += 1


def embed(
    items: list[list[str | bytes]],
    input_type: str,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[list[float]]:
    """Embeds each item (a list of text strings and PNG `bytes` combined into
    one vector). input_type: "search_document" or "search_query". Returns one
    EMBED_DIMENSION-wide vector per item, in order."""
    vectors: list[list[float]] = []
    for start in range(0, len(items), EMBED_BATCH_SIZE):
        batch = items[start : start + EMBED_BATCH_SIZE]
        inputs = [{"content": [_embed_segment(segment) for segment in item]} for item in batch]
        data = _post(
            "/embed",
            {
                "model": EMBED_MODEL,
                "input_type": input_type,
                "embedding_types": ["float"],
                "output_dimension": EMBED_DIMENSION,
                "inputs": inputs,
            },
            sleep_fn,
        )
        floats = (data.get("embeddings") or {}).get("float")
        if not isinstance(floats, list) or len(floats) != len(batch):
            raise CohereError(f"Cohere returned {len(floats) if isinstance(floats, list) else 'no'} embeddings for {len(batch)} inputs")
        if any(
            not isinstance(vector, list) or len(vector) != EMBED_DIMENSION
            or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)
            for vector in floats
        ):
            raise CohereError("Cohere returned invalid embedding dimensions or values")
        vectors.extend(floats)
    return vectors


def _embed_segment(segment: str | bytes) -> dict:
    if isinstance(segment, bytes):
        uri = "data:image/png;base64," + base64.b64encode(segment).decode()
        return {"type": "image_url", "image_url": {"url": uri}}
    return {"type": "text", "text": segment}
