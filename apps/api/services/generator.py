import logging
import os
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai import types
from google.genai.errors import APIError

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"

# Citation markers are positional, 1-indexed into the `chunks` list passed
# to generate() — NOT the chunk's database id. The model only ever sees
# chunks numbered [1]..[len(chunks)] in the prompt; it has no way to know
# a chunk's real chunk_id, so it cannot cite one. FEAT-012 (the /query
# endpoint, the first real caller of this service) must map each cited
# position back to `chunks[position - 1].chunk_id` itself — GenerateResult
# deliberately returns positions, not ids, to make that mapping explicit
# and unambiguous rather than requiring FEAT-012 to reverse-engineer it.
SYSTEM_INSTRUCTION = (
    "You are Docify's question-answering assistant. Answer the user's question using ONLY "
    "the numbered context chunks provided below — never rely on outside knowledge. Every "
    "factual claim in your answer must be followed by an inline citation marker in the form "
    "[N], where N is the number printed immediately before the chunk you drew that claim "
    "from. That number is the chunk's position in this prompt (the first chunk is [1], the "
    "second is [2], and so on) — it is NOT any database id or other identifier. Cite every "
    "chunk you rely on; a claim with no [N] marker will be treated as unsupported. Never "
    "invent a marker number higher than the number of chunks you were given. If the provided "
    "chunks do not contain enough information to answer, say so plainly instead of guessing."
)

# Gemini was observed live grouping multiple citations into one bracket —
# `[2, 3]`, `[1, 2, 4]` — despite the system prompt asking for the
# singular `[N]` form (FEAT-010's generation-quality test against
# table_heavy.pdf: 2 of 4 real answers did this). A first fix matched
# `\[(\d+(?:\s*,\s*\d+)*)\]` — comma-separated digits only — but a
# 2026-07-24 self-audit found that was still delimiter-specific: a
# semicolon variant, `[2; 3]`, reproduces the identical silent-drop
# failure the comma fix was meant to close. Rather than special-case
# delimiters one at a time (comma today, semicolon tomorrow, "and" the
# day after), this matches ANY non-nested bracket span and then pulls
# every integer out of its contents regardless of what separates them —
# the invariant that actually holds is "citations are digit runs inside
# a bracket," not "citations are comma-separated." Malformed nested
# brackets (`[2, [3]]`) are a deliberate, accepted gap: the inner `[3]`
# still parses as its own bracket, but content preceding a nested `[`
# is dropped rather than guessed at — judged reasonably out-of-scope,
# not a shape Gemini has been observed producing.
#
# Public (not `_`-prefixed): FEAT-012 needs the IDENTICAL bracket/number
# parsing to extract claim spans and strip dropped-citation markers from
# the answer text — reusing these constants directly guarantees claim
# extraction and citation validation can never silently disagree on what
# counts as a marker, rather than risking a second, independently
# maintained regex drifting from this one over time.
CITATION_BRACKET = re.compile(r"\[([^\[\]]*)\]")
# `-?` matters: without it, `\d+` strips a leading minus sign from `[-1]`
# and silently extracts "1" as a VALID citation to chunk 1 — a self-audit
# found this treats `[-1]` differently from `[0]` (correctly flagged
# hallucinated, since 0 is out of the 1-indexed range), when both should
# fail the same way. Capturing the sign lets `int("-1") == -1` fail the
# same `1 <= n <= num_chunks` range check `[0]` already fails.
CITATION_NUMBER = re.compile(r"-?\d+")


class GenerationError(Exception):
    """Non-transient generation failure — auth, invalid request, a Gemini
    API error, or a response with no usable text (e.g. blocked by safety
    filters). Never raised for a successful response, even one containing
    hallucinated citation markers — that case is surfaced via
    GenerateResult.hallucinated_markers, not an exception, since a bad
    citation index is a data-quality issue for the caller to handle, not
    a failure of the generation call itself."""


@dataclass
class GeneratorChunk:
    """Generator's own input contract — deliberately not FEAT-009's
    RetrievedChunk, since RetrievedChunk carries no image data (the RPC
    functions it calls never SELECT figure_path) and Generator has no
    Storage access of its own. The caller (FEAT-012) is responsible for
    turning RetrievedChunk rows into these, fetching each figure chunk's
    image bytes from Storage first."""

    chunk_id: str
    content: str
    element_type: str
    page_number: int
    document_name: str
    image: bytes | None = None  # PNG bytes; only meaningful when element_type == "figure"

    def __post_init__(self):
        # A self-audit found this had no protection at all: a caller that
        # forgets to fetch/attach a figure's image silently gets a
        # text-only chunk sent to Gemini, no error, no warning. This is
        # never legitimate — parser.py already drops any figure element
        # whose image extraction failed (it never becomes a chunk at
        # all), so a figure-typed GeneratorChunk reaching here with no
        # image can only mean the caller's Storage fetch/adaptation step
        # is broken, not a real state to silently tolerate.
        if self.element_type == "figure" and self.image is None:
            raise ValueError(
                f"GeneratorChunk {self.chunk_id!r} has element_type='figure' but image=None — "
                "figure chunks always have an image by the time they reach ingestion's chunks "
                "table, so a missing image here means the caller failed to fetch it from "
                "Storage, not a legitimate text-only figure"
            )


@dataclass
class GenerateResult:
    answer: str
    # 1-indexed positions into the `chunks` argument that the model cited
    # and that actually correspond to a provided chunk — deduped, in
    # first-appearance order.
    cited_indices: list[int]
    # [N] markers the model emitted that do NOT correspond to any
    # provided chunk (N <= 0 or N > len(chunks)) — surfaced, never
    # silently dropped or allowed to crash the caller. Always empty in
    # the common case.
    hallucinated_markers: list[int]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


def _default_client() -> genai.Client:
    # The SDK's automatic env-var detection looks for GOOGLE_API_KEY, not
    # GEMINI_API_KEY (this project's actual env var, shared with the OCR
    # fallback path) — must be passed explicitly. See .agent/api-docs/
    # gemini.md, verified against the installed SDK source directly.
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _build_contents(question: str, chunks: list[GeneratorChunk]) -> list[types.Part]:
    parts: list[types.Part] = []
    for i, chunk in enumerate(chunks, start=1):
        header = f"[{i}] (page {chunk.page_number}, {chunk.element_type}, from {chunk.document_name}):"
        parts.append(types.Part.from_text(text=f"{header}\n{chunk.content}"))
        if chunk.image is not None:
            parts.append(types.Part.from_bytes(data=chunk.image, mime_type="image/png"))
    parts.append(types.Part.from_text(text=f"\nQuestion: {question}"))
    return parts


def _parse_citations(answer: str, num_chunks: int) -> tuple[list[int], list[int]]:
    cited: list[int] = []
    hallucinated: list[int] = []
    seen_cited: set[int] = set()
    seen_hallucinated: set[int] = set()

    for bracket in CITATION_BRACKET.finditer(answer):
        for raw in CITATION_NUMBER.finditer(bracket.group(1)):
            n = int(raw.group())
            if 1 <= n <= num_chunks:
                if n not in seen_cited:
                    seen_cited.add(n)
                    cited.append(n)
            elif n not in seen_hallucinated:
                seen_hallucinated.add(n)
                hallucinated.append(n)

    if hallucinated:
        logger.warning(
            "generator: model emitted %d hallucinated citation marker(s) out of range 1..%d: %s",
            len(hallucinated),
            num_chunks,
            hallucinated,
        )

    return cited, hallucinated


class Generator:
    def __init__(self, client: genai.Client | None = None):
        self._client = client or _default_client()

    def generate(self, question: str, chunks: list[GeneratorChunk]) -> GenerateResult:
        if not chunks:
            raise GenerationError("generate() requires at least one context chunk")

        contents = _build_contents(question, chunks)
        config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, temperature=0.2)

        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(model=MODEL, contents=contents, config=config)
        except APIError as exc:
            raise GenerationError(f"Gemini generation failed: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        answer = response.text
        if answer is None:
            raise GenerationError(f"Gemini returned no text content (prompt_feedback={response.prompt_feedback})")

        cited_indices, hallucinated_markers = _parse_citations(answer, len(chunks))
        usage = response.usage_metadata

        return GenerateResult(
            answer=answer,
            cited_indices=cited_indices,
            hallucinated_markers=hallucinated_markers,
            model=response.model_version or MODEL,
            input_tokens=usage.prompt_token_count if usage and usage.prompt_token_count is not None else 0,
            output_tokens=usage.candidates_token_count if usage and usage.candidates_token_count is not None else 0,
            latency_ms=latency_ms,
        )
