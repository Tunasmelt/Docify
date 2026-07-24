import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum

import httpx
import pydantic
from google import genai
from google.genai import types
from google.genai.errors import APIError

from services.generator import GeneratorChunk

logger = logging.getLogger(__name__)

MODEL = "gemini-3.5-flash-lite"

SYSTEM_INSTRUCTION = (
    "You are Docify's citation verifier. You will be given a SOURCE (the exact chunk of a "
    "document a claim was cited from) and a CLAIM (a factual statement from a generated "
    "answer that cited this source). Decide whether the SOURCE actually supports the CLAIM:\n"
    '- "supported": every part of the claim is directly stated or clearly implied by the source.\n'
    '- "partial": the source discusses the same topic as the claim, but the claim adds, '
    "changes, or overreaches beyond what the source actually says.\n"
    '- "unsupported": the source does not support the claim at all — it is about a different '
    "topic, contradicts the claim, or the claim is fabricated.\n"
    'If verdict is "supported" or "partial", quote must be the exact supporting span copied '
    'verbatim from the source (never paraphrased). If verdict is "unsupported", quote must be '
    "null. Never invent a quote that does not appear in the source."
)


class VerdictLabel(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class _VerdictResponse(pydantic.BaseModel):
    # Structured output (response_schema, verified against the installed
    # google-genai SDK source — see .agent/api-docs/gemini.md) instead of
    # free-text parsing. FEAT-010's citation-marker regex needed three
    # separate rounds of fixes (grouped brackets, delimiters, sign
    # handling) because it parsed free text; letting the SDK validate
    # against a schema closes that entire failure class here rather than
    # re-deriving the same lesson through a second round of live bugs.
    verdict: VerdictLabel
    quote: str | None = None


@dataclass
class Verdict:
    verdict: VerdictLabel
    quote: str | None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    # Non-None ONLY when the Gemini call itself failed or returned a
    # response that didn't conform to the schema — verdict is ALWAYS
    # forced to UNSUPPORTED and quote to None in that case, regardless of
    # what a caller does with this field. This is what makes fail-safe
    # behavior structural rather than a convention the caller must
    # remember to uphold: even a caller that only ever reads `.verdict`
    # and ignores `.error` entirely still gets the safe outcome.
    error: str | None = None


class VerificationError(Exception):
    """Raised only for a caller-side misuse (e.g. an empty claim_text) —
    never for a failed or malformed Gemini response, which verify()
    converts into a fail-safe UNSUPPORTED Verdict instead of an
    exception a caller could forget to catch and mishandle."""


def _default_client() -> genai.Client:
    # Same GEMINI_API_KEY-vs-GOOGLE_API_KEY note as generator.py — the
    # SDK's auto-detection looks for GOOGLE_API_KEY, not this project's
    # actual env var, so it must be passed explicitly.
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _build_contents(claim_text: str, chunk: GeneratorChunk) -> list[types.Part]:
    header = f"SOURCE (page {chunk.page_number}, {chunk.element_type}, from {chunk.document_name}):"
    parts: list[types.Part] = [types.Part.from_text(text=f"{header}\n{chunk.content}")]
    if chunk.image is not None:
        parts.append(types.Part.from_bytes(data=chunk.image, mime_type="image/png"))
    parts.append(types.Part.from_text(text=f"\nCLAIM: {claim_text}"))
    return parts


_WHITESPACE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _quote_is_grounded(quote: str, content: str) -> bool:
    # A self-audit found the model's returned quote was trusted as-is,
    # with no check it actually appears anywhere in the source — a
    # verifier whose entire job is catching plausible-sounding
    # falsehoods was itself trusting one. Whitespace is normalized
    # (collapsed runs of whitespace, not exact byte-for-byte) rather than
    # requiring a strict substring match, since real chunk content (e.g.
    # markdown tables with padding) can differ from the model's
    # reproduction by whitespace alone without the quote being fabricated.
    return _normalize_whitespace(quote) in _normalize_whitespace(content)


def _fail_safe_verdict(model: str, error: str, latency_ms: float) -> Verdict:
    return Verdict(
        verdict=VerdictLabel.UNSUPPORTED,
        quote=None,
        model=model,
        input_tokens=0,
        output_tokens=0,
        latency_ms=latency_ms,
        error=error,
    )


class Verifier:
    def __init__(self, client: genai.Client | None = None):
        self._client = client or _default_client()

    def verify(self, claim_text: str, chunk: GeneratorChunk) -> Verdict:
        if not claim_text or not claim_text.strip():
            raise VerificationError("verify() requires a non-empty claim_text")

        contents = _build_contents(claim_text, chunk)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_VerdictResponse,
            temperature=0.0,
        )

        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(model=MODEL, contents=contents, config=config)
        except APIError as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning("verifier: Gemini call failed — failing safe to UNSUPPORTED: %s", exc)
            return _fail_safe_verdict(MODEL, f"Gemini API error: {exc}", latency_ms)
        except httpx.HTTPError as exc:
            # A self-audit found this branch missing: the SDK's own HTTP
            # layer (_api_client.py) calls httpx directly with no
            # try/except around it, so a genuine timeout or connection
            # failure raises a raw httpx exception BEFORE the SDK ever
            # gets a response to wrap into APIError — confirmed live, a
            # mocked httpx.ReadTimeout crashed verify() uncaught before
            # this branch existed. httpx.HTTPError is the base for both
            # transport failures (timeout, connection refused, DNS) and
            # HTTP status errors, so this closes that gap the same
            # fail-safe way as an APIError.
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning("verifier: Gemini call failed at the transport layer — failing safe to UNSUPPORTED: %s", exc)
            return _fail_safe_verdict(MODEL, f"transport error: {exc}", latency_ms)
        latency_ms = (time.perf_counter() - started) * 1000

        # response.parsed is None both when the SDK never attempted to
        # parse (no candidates) AND when it tried and the JSON was
        # malformed or didn't validate against _VerdictResponse — the SDK
        # silently swallows ValidationError/JSONDecodeError internally
        # (google/genai/types.py) rather than raising, so this check is
        # the only place that failure becomes visible. Must fail safe
        # here exactly like the APIError branch above, not assume success.
        if response.parsed is None:
            logger.warning(
                "verifier: response did not conform to the verdict schema — failing safe to "
                "UNSUPPORTED. raw text=%r",
                response.text,
            )
            return _fail_safe_verdict(response.model_version or MODEL, "unparseable or non-schema-conforming response", latency_ms)

        parsed: _VerdictResponse = response.parsed
        usage = response.usage_metadata

        # Defensive, not just prompted: an unsupported verdict must never
        # carry a quote even if the model deviates from instructions and
        # emits one anyway (the same "don't just trust the prompt, enforce
        # structurally" lesson FEAT-010's citation-parsing gaps taught).
        quote = parsed.quote if parsed.verdict != VerdictLabel.UNSUPPORTED else None

        # A self-audit found this check was entirely missing: a
        # SUPPORTED/PARTIAL verdict's quote was trusted as-is from the
        # model, with nothing confirming it actually appears in the real
        # source content — a fabricated-but-plausible quote passed
        # straight through as "verified" (proven live before this
        # existed). If the quote isn't grounded in the chunk, the verdict
        # that depends on it can't be trusted either — fail safe exactly
        # like a broken API call, not just the quote field.
        if quote is not None and not _quote_is_grounded(quote, chunk.content):
            logger.warning(
                "verifier: model returned verdict=%s with a quote not found in the source — "
                "failing safe to UNSUPPORTED. quote=%r",
                parsed.verdict.value,
                quote,
            )
            return _fail_safe_verdict(
                response.model_version or MODEL, f"returned quote not found in source content: {quote!r}", latency_ms
            )

        return Verdict(
            verdict=parsed.verdict,
            quote=quote,
            model=response.model_version or MODEL,
            input_tokens=usage.prompt_token_count if usage and usage.prompt_token_count is not None else 0,
            output_tokens=usage.candidates_token_count if usage and usage.candidates_token_count is not None else 0,
            latency_ms=latency_ms,
        )

    def verify_batch(self, pairs: list[tuple[str, GeneratorChunk]]) -> list[Verdict]:
        """Verifies every (claim_text, chunk) pair from one generate()
        call's answer concurrently — each pair is an independent,
        unrelated Gemini call, the same shape as Retriever's parallel
        vector/FTS searches (FEAT-009). Order of results matches order
        of input pairs, regardless of completion order."""
        if not pairs:
            return []
        with ThreadPoolExecutor(max_workers=min(len(pairs), 8)) as pool:
            futures = [pool.submit(self.verify, claim_text, chunk) for claim_text, chunk in pairs]
            return [future.result() for future in futures]
