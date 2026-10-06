import logging
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum

import httpx
import pydantic
from google import genai
from google.genai import types
from google.genai.errors import APIError

from services import cohere_client
from services.gemini_retry import call_with_retry, can_fall_back_to_cohere

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
    # 2026-08-02 — distinct from UNSUPPORTED, added after finding this
    # project's own fail-safe path was conflating two different things:
    # "checked and found false" (a real model verdict, or a caught
    # attempt to pass off a fabricated/ungrounded quote) vs. "never
    # actually checked" (the verify call itself errored, timed out, or
    # returned a malformed/non-schema response — an infrastructure
    # failure, not a judgment about the claim). The citation recovery
    # cascade (2026-08-02, same day) made this matter for a larger share
    # of real answers than before: more citations now reach verify_batch()
    # at all, so more of them can hit a real infrastructure failure here
    # too. UNVERIFIED is used ONLY for that narrow "could not run"
    # category — see _fail_safe_verdict's own docstring for exactly which
    # branches use it and, just as importantly, which ones deliberately
    # still use UNSUPPORTED.
    UNVERIFIED = "unverified"


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


class _BatchVerdictItem(pydantic.BaseModel):
    item: int
    verdict: VerdictLabel
    quote: str | None = None


class _CohereBatchResponse(pydantic.BaseModel):
    # Cohere's JSON mode needs an object at the root, so the batch is wrapped.
    results: list[_BatchVerdictItem]


_VERDICT_ENUM = {"type": "string", "enum": ["supported", "partial", "unsupported"]}
_COHERE_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": _VERDICT_ENUM, "quote": {"type": "string"}},
    "required": ["verdict"],
}
_COHERE_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"item": {"type": "integer"}, "verdict": _VERDICT_ENUM, "quote": {"type": "string"}},
                "required": ["item", "verdict"],
            },
        }
    },
    "required": ["results"],
}

BATCH_SIZE = 12

BATCH_SYSTEM_INSTRUCTION = (
    SYSTEM_INSTRUCTION
    + "\n\nYou will be given several numbered ITEMs, each with its own SOURCE and CLAIM. Judge "
    "each ITEM independently, using only that ITEM's SOURCE. Return one result per ITEM, with "
    "`item` set to its number."
)


@dataclass
class Verdict:
    verdict: VerdictLabel
    quote: str | None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    # Non-None whenever something went wrong (an infrastructure failure
    # OR a caught fabricated/ungrounded quote) — quote is always forced
    # to None alongside it, regardless of what a caller does with this
    # field. This is what makes fail-safe behavior structural rather than
    # a convention the caller must remember to uphold: even a caller that
    # only ever reads `.verdict` and ignores `.error` entirely still gets
    # a safe outcome. `.verdict` itself is what actually distinguishes
    # the two failure shapes (2026-08-02): UNVERIFIED when the call
    # genuinely could not run or produce a trustworthy response at all
    # (error/timeout/malformed response); UNSUPPORTED when the call DID
    # run and DID return something, but a caught fabricated/ungrounded
    # quote means the response itself can't be trusted — see
    # _fail_safe_verdict's docstring for the exact line between them.
    error: str | None = None


class VerificationError(Exception):
    """Raised only for a caller-side misuse (e.g. an empty claim_text) —
    never for a failed or malformed Gemini response, which verify()
    converts into a fail-safe Verdict (UNVERIFIED or UNSUPPORTED,
    depending on the failure — never a plain exception a caller could
    forget to catch and mishandle)."""


def _default_client() -> genai.Client:
    # Same GEMINI_API_KEY-vs-GOOGLE_API_KEY note as generator.py — the
    # SDK's auto-detection looks for GOOGLE_API_KEY, not this project's
    # actual env var, so it must be passed explicitly.
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _build_batch_contents(pairs: list[tuple[str, GeneratorChunk]]) -> list[types.Part]:
    parts: list[types.Part] = []
    for index, (claim_text, chunk) in enumerate(pairs, start=1):
        header = f"ITEM {index}\nSOURCE (page {chunk.page_number}, {chunk.element_type}, from {chunk.document_name}):"
        parts.append(types.Part.from_text(text=f"{header}\n{chunk.content}"))
        if chunk.image is not None:
            parts.append(types.Part.from_bytes(data=chunk.image, mime_type="image/png"))
        parts.append(types.Part.from_text(text=f"CLAIM {index}: {claim_text}\n"))
    return parts


def _build_contents(claim_text: str, chunk: GeneratorChunk) -> list[types.Part]:
    header = f"SOURCE (page {chunk.page_number}, {chunk.element_type}, from {chunk.document_name}):"
    parts: list[types.Part] = [types.Part.from_text(text=f"{header}\n{chunk.content}")]
    if chunk.image is not None:
        parts.append(types.Part.from_bytes(data=chunk.image, mime_type="image/png"))
    parts.append(types.Part.from_text(text=f"\nCLAIM: {claim_text}"))
    return parts


def _build_text_prompt(claim_text: str, chunk: GeneratorChunk) -> str:
    header = f"SOURCE (page {chunk.page_number}, {chunk.element_type}, from {chunk.document_name}):"
    return f"{header}\n{chunk.content}\n\nCLAIM: {claim_text}"


def _build_batch_text_prompt(pairs: list[tuple[str, GeneratorChunk]]) -> str:
    blocks = []
    for index, (claim_text, chunk) in enumerate(pairs, start=1):
        header = f"ITEM {index}\nSOURCE (page {chunk.page_number}, {chunk.element_type}, from {chunk.document_name}):"
        blocks.append(f"{header}\n{chunk.content}\nCLAIM {index}: {claim_text}\n")
    return "\n".join(blocks)


_WHITESPACE = re.compile(r"\s+")
_TABLE_SEPARATOR_CELL = re.compile(r"(?<![\w-])-{3,}(?![\w-])")
_TYPOGRAPHY = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
        "|": " ",
    }
)


def _normalize_for_grounding(text: str) -> str:
    """Normalizes away differences that don't change what a quote says:
    Unicode compatibility forms, curly quotes and dash variants, case,
    markdown table syntax (cell pipes and `---` separator rows — table
    chunks are stored as markdown, so "Q3 $4.20M" must match
    "| Q3 | $4.20M |"), and runs of whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)
    text = _TABLE_SEPARATOR_CELL.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _quote_is_grounded(quote: str, content: str) -> bool:
    # The model's quote must actually appear in the source — a verifier
    # whose job is catching plausible-sounding falsehoods can't trust one
    # itself. Formatting-only differences are normalized away (see
    # _normalize_for_grounding); the words themselves must match.
    return _normalize_for_grounding(quote) in _normalize_for_grounding(content)


def _fail_safe_verdict(verdict_label: VerdictLabel, model: str, error: str, latency_ms: float) -> Verdict:
    """The one place every failure path in verify() below funnels
    through — `verdict_label` is the ONE thing callers must get right,
    since it's the real, structural line between the two failure shapes
    (2026-08-02):

    - `VerdictLabel.UNVERIFIED` — the call genuinely could not run or
      produce a trustworthy response: a raised APIError/httpx.HTTPError
      (infrastructure — quota, network, auth), or `response.parsed is
      None` (the call completed but the response was malformed/didn't
      conform to the schema at all — a protocol-level failure, not a
      judgment about the claim).
    - `VerdictLabel.UNSUPPORTED` — the call DID run and DID return a
      real, schema-conforming response, but it's being rejected because
      the quote it offered as evidence doesn't actually appear in the
      source (`_quote_is_grounded` below). This is deliberately NOT
      UNVERIFIED: catching a model trying to pass off a fabricated quote
      is closer to "we checked, and what it offered as proof doesn't
      hold up" than "we never checked" — the stronger, dropped-not-shown
      treatment stays warranted.

    Both cases force `quote=None` — an error, by definition, means
    nothing survived to be shown as a supporting quote either way."""
    return Verdict(
        verdict=verdict_label,
        quote=None,
        model=model,
        input_tokens=0,
        output_tokens=0,
        latency_ms=latency_ms,
        error=error,
    )


class Verifier:
    def __init__(self, client: genai.Client | None = None, *, retry_sleep=time.sleep):
        self._client = client or _default_client()
        self._retry_sleep = retry_sleep

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
            response = call_with_retry(
                lambda: self._client.models.generate_content(model=MODEL, contents=contents, config=config),
                what="verifier",
                sleep_fn=self._retry_sleep,
            )
        except APIError as exc:
            fallback = self._verify_with_cohere(claim_text, chunk, exc)
            if fallback is not None:
                return fallback
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning("verifier: Gemini call failed — failing safe to UNVERIFIED: %s", exc)
            return _fail_safe_verdict(VerdictLabel.UNVERIFIED, MODEL, f"Gemini API error: {exc}", latency_ms)
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
            fallback = self._verify_with_cohere(claim_text, chunk, exc)
            if fallback is not None:
                return fallback
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning("verifier: Gemini call failed at the transport layer — failing safe to UNVERIFIED: %s", exc)
            return _fail_safe_verdict(VerdictLabel.UNVERIFIED, MODEL, f"transport error: {exc}", latency_ms)
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
                "UNVERIFIED. raw text=%r",
                response.text,
            )
            return _fail_safe_verdict(
                VerdictLabel.UNVERIFIED, response.model_version or MODEL, "unparseable or non-schema-conforming response", latency_ms
            )

        parsed: _VerdictResponse = response.parsed
        usage = response.usage_metadata
        return _finalize(
            parsed.verdict,
            parsed.quote,
            chunk,
            model=response.model_version or MODEL,
            input_tokens=usage.prompt_token_count if usage and usage.prompt_token_count is not None else 0,
            output_tokens=usage.candidates_token_count if usage and usage.candidates_token_count is not None else 0,
            latency_ms=latency_ms,
        )

    def _verify_with_cohere(self, claim_text: str, chunk: GeneratorChunk, gemini_exc: Exception) -> Verdict | None:
        """The Cohere fallback for one claim, or None when it doesn't apply
        (Gemini's failure wasn't transient, no key, a figure image Cohere
        can't see) or itself fails — the caller then keeps its fail-safe."""
        if not can_fall_back_to_cohere(gemini_exc) or chunk.image is not None:
            return None
        logger.warning("verifier: Gemini failed transiently (%s) — verifying with Cohere instead", gemini_exc)
        started = time.perf_counter()
        try:
            result = cohere_client.chat(
                SYSTEM_INSTRUCTION,
                _build_text_prompt(claim_text, chunk),
                json_schema=_COHERE_VERDICT_SCHEMA,
                temperature=0.0,
                sleep_fn=self._retry_sleep,
            )
            parsed = _VerdictResponse.model_validate_json(result.text)
        except (cohere_client.CohereError, pydantic.ValidationError) as exc:
            logger.warning("verifier: Cohere fallback failed (%s) — failing safe to UNVERIFIED", exc)
            return None
        return _finalize(
            parsed.verdict,
            parsed.quote,
            chunk,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _verify_group_with_cohere(
        self, pairs: list[tuple[str, GeneratorChunk]], gemini_exc: Exception
    ) -> list[Verdict] | None:
        """Cohere fallback for a whole group in one call, or None (the caller
        then verifies each claim on its own, as it does for any group failure)."""
        if not can_fall_back_to_cohere(gemini_exc) or any(chunk.image is not None for _claim, chunk in pairs):
            return None
        logger.warning("verifier: Gemini batch failed transiently (%s) — verifying the group with Cohere", gemini_exc)
        started = time.perf_counter()
        try:
            result = cohere_client.chat(
                BATCH_SYSTEM_INSTRUCTION,
                _build_batch_text_prompt(pairs),
                json_schema=_COHERE_BATCH_SCHEMA,
                temperature=0.0,
                sleep_fn=self._retry_sleep,
            )
            parsed = _CohereBatchResponse.model_validate_json(result.text).results
        except (cohere_client.CohereError, pydantic.ValidationError) as exc:
            logger.warning("verifier: Cohere batch fallback failed (%s) — verifying each claim separately", exc)
            return None
        return self._group_verdicts(
            pairs, parsed, result.model, result.input_tokens, result.output_tokens, (time.perf_counter() - started) * 1000
        )

    def verify_batch(self, pairs: list[tuple[str, GeneratorChunk]]) -> list[Verdict]:
        """Verifies every (claim_text, chunk) pair from one answer. Claims are
        checked in as few Gemini calls as possible — groups of up to
        BATCH_SIZE in one call each — instead of one call per claim, which
        burned the free-tier request quota fast (up to 8 parallel calls per
        answer). Each verdict gets exactly the same quote checks as verify().
        If a group's call fails or its response is malformed, that group
        falls back to one call per claim; any claim missing from a batch
        response is verified on its own. Order matches `pairs`."""
        if not pairs:
            return []
        if len(pairs) == 1:
            return [self.verify(*pairs[0])]
        results: list[Verdict | None] = [None] * len(pairs)
        groups = [list(range(i, min(i + BATCH_SIZE, len(pairs)))) for i in range(0, len(pairs), BATCH_SIZE)]
        for group in groups:
            batch = self._verify_group([pairs[i] for i in group])
            if batch is None:
                batch = self._verify_each([pairs[i] for i in group])
            for i, verdict in zip(group, batch):
                results[i] = verdict
        return results  # type: ignore[return-value]  # every slot is filled above

    def _verify_each(self, pairs: list[tuple[str, GeneratorChunk]]) -> list[Verdict]:
        """One call per claim, concurrently — the fallback path. A failure on
        one pair never affects another. Order matches `pairs`."""
        if not pairs:
            return []
        with ThreadPoolExecutor(max_workers=min(len(pairs), 8)) as pool:
            futures = [pool.submit(self.verify, claim_text, chunk) for claim_text, chunk in pairs]
            return [future.result() for future in futures]

    def _verify_group(self, pairs: list[tuple[str, GeneratorChunk]]) -> list[Verdict] | None:
        """One Gemini call for the whole group, or None if the call failed or
        returned nothing usable (the caller then falls back to _verify_each)."""
        for claim_text, _chunk in pairs:
            if not claim_text or not claim_text.strip():
                raise VerificationError("verify_batch() requires non-empty claim texts")
        config = types.GenerateContentConfig(
            system_instruction=BATCH_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=list[_BatchVerdictItem],
            temperature=0.0,
        )
        started = time.perf_counter()
        try:
            response = call_with_retry(
                lambda: self._client.models.generate_content(
                    model=MODEL, contents=_build_batch_contents(pairs), config=config
                ),
                what="batch verifier",
                sleep_fn=self._retry_sleep,
            )
        except (APIError, httpx.HTTPError) as exc:
            logger.warning("verifier: batch call failed (%s) — verifying each claim separately", type(exc).__name__)
            return self._verify_group_with_cohere(pairs, exc)
        latency_ms = (time.perf_counter() - started) * 1000

        parsed = getattr(response, "parsed", None)
        if not isinstance(parsed, list) or not all(isinstance(item, _BatchVerdictItem) for item in parsed):
            logger.warning("verifier: batch response did not match the schema — verifying each claim separately")
            return None

        usage = response.usage_metadata
        return self._group_verdicts(
            pairs,
            parsed,
            response.model_version or MODEL,
            usage.prompt_token_count if usage and usage.prompt_token_count is not None else 0,
            usage.candidates_token_count if usage and usage.candidates_token_count is not None else 0,
            latency_ms,
        )

    def _group_verdicts(
        self,
        pairs: list[tuple[str, GeneratorChunk]],
        parsed: list[_BatchVerdictItem],
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
    ) -> list[Verdict]:
        """Turns one batch response (Gemini's or Cohere's) into a verdict per pair."""
        by_item: dict[int, _BatchVerdictItem] = {}
        for item in parsed:
            if 1 <= item.item <= len(pairs) and item.item not in by_item:
                by_item[item.item] = item
        share = max(1, len(pairs))

        verdicts: list[Verdict] = []
        for index, (claim_text, chunk) in enumerate(pairs, start=1):
            item = by_item.get(index)
            if item is None:
                logger.warning("verifier: batch response omitted item %d — verifying it on its own", index)
                verdicts.append(self.verify(claim_text, chunk))
                continue
            verdicts.append(
                _finalize(
                    item.verdict,
                    item.quote,
                    chunk,
                    model=model,
                    input_tokens=input_tokens // share,
                    output_tokens=output_tokens // share,
                    latency_ms=latency_ms,
                )
            )
        return verdicts


def _finalize(
    verdict: VerdictLabel,
    quote: str | None,
    chunk: GeneratorChunk,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
) -> Verdict:
    """The checks every model verdict goes through, single or batched."""
    if verdict == VerdictLabel.UNVERIFIED:
        # Only this code assigns UNVERIFIED (when verification couldn't run);
        # a model claiming it is treated as an unusable answer.
        return _fail_safe_verdict(VerdictLabel.UNVERIFIED, model, "model returned 'unverified'", latency_ms)

    # Defensive, not just prompted: an unsupported verdict must never carry a
    # quote even if the model deviates from instructions and emits one anyway.
    if verdict == VerdictLabel.UNSUPPORTED:
        quote = None

    if quote is not None and chunk.image is not None and not _quote_is_grounded(quote, chunk.content):
        # A figure chunk's text is only its caption; the model also saw the
        # image and may legitimately quote text read off it, which can't be
        # checked against stored text. Keep the verdict, but don't display a
        # quote nothing on our side can confirm.
        logger.info("verifier: figure quote not in caption text — keeping verdict, omitting quote. quote=%r", quote)
        quote = None

    # The quote must actually appear in the source: a fabricated-but-plausible
    # quote means the verdict that depends on it can't be trusted either.
    if quote is not None and not _quote_is_grounded(quote, chunk.content):
        logger.warning(
            "verifier: model returned verdict=%s with a quote not found in the source — failing safe to "
            "UNSUPPORTED. quote=%r",
            verdict.value,
            quote,
        )
        return _fail_safe_verdict(
            VerdictLabel.UNSUPPORTED, model, f"returned quote not found in source content: {quote!r}", latency_ms
        )

    return Verdict(
        verdict=verdict,
        quote=quote,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
