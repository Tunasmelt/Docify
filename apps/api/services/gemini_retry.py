"""Bounded retries for interactive Gemini calls (generation, verification).

A single transient 429/5xx used to fail the whole request: generation
returned GENERATE_FAILED, and verification marked citations `unverified`.
These helpers retry those failures a few times, but never make a user wait
long: if the server asks for a longer back-off than MAX_DELAY_SECONDS, we give
up at once instead (the embedding fallback, which runs in a background ingest,
has its own longer-waiting retry in services/embedder.py).
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from google.genai.errors import APIError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # 1 initial attempt + up to 2 retries
BACKOFF_SECONDS = (1.0, 2.0)  # used when the server doesn't say how long to wait
MAX_DELAY_SECONDS = 8.0  # longest wait we'll impose on an interactive request

_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
_TRANSIENT_STATUSES = {"RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED"}

T = TypeVar("T")


def retry_info_delay_seconds(exc: Exception) -> float | None:
    """The server-provided retryDelay from a Gemini error payload (a
    google.rpc.RetryInfo entry in error.details[], e.g. '15s'), or None if
    absent or malformed."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        for item in details.get("error", {}).get("details", []) or []:
            if not isinstance(item, dict) or not str(item.get("@type", "")).endswith("RetryInfo"):
                continue
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            return float(match.group(1)) if match else None
    return None


def is_transient(exc: Exception) -> bool:
    """Rate limits, server errors, and network failures. Bad requests and auth
    errors are never retried — retrying can't fix them."""
    if isinstance(exc, APIError):
        return exc.code in _TRANSIENT_HTTP_CODES or exc.status in _TRANSIENT_STATUSES
    return isinstance(exc, httpx.TransportError)


def _retry_delay(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before the next attempt, or None to stop retrying."""
    if attempt >= MAX_ATTEMPTS or not is_transient(exc):
        return None
    delay = retry_info_delay_seconds(exc)
    if delay is None:
        delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
    return delay if delay <= MAX_DELAY_SECONDS else None


def call_with_retry(fn: Callable[[], T], *, what: str, sleep_fn: Callable[[float], None] = time.sleep) -> T:
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:
            delay = _retry_delay(exc, attempt)
            if delay is None:
                raise
            logger.warning("%s failed transiently (attempt %d/%d: %s) — retrying in %.1fs", what, attempt, MAX_ATTEMPTS, exc, delay)
            sleep_fn(delay)
            attempt += 1


async def call_with_retry_async(
    fn: Callable[[], Awaitable[T]], *, what: str, sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep
) -> T:
    attempt = 1
    while True:
        try:
            return await fn()
        except Exception as exc:
            delay = _retry_delay(exc, attempt)
            if delay is None:
                raise
            logger.warning("%s failed transiently (attempt %d/%d: %s) — retrying in %.1fs", what, attempt, MAX_ATTEMPTS, exc, delay)
            await sleep_fn(delay)
            attempt += 1
