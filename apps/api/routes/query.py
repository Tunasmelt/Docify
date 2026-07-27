import logging
import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.query import CitationResponse, QueryMetadata, QueryRequest, QueryResponse
from services.figure_fetcher import fetch_generator_chunks, signed_figure_url
from services.generator import CITATION_BRACKET, CITATION_NUMBER, GenerationError, Generator, GeneratorChunk
from services.retriever import Retriever
from services.verifier import Verdict, VerdictLabel, Verifier

logger = logging.getLogger(__name__)

router = APIRouter()

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

# Conversation memory window (2026-07-27 follow-up): last 5 turns (10
# messages: 5 user + 5 assistant), not the full conversation history.
# Real justification, not an arbitrary round number: ordinary follow-up
# ambiguity ("that", "it", "how does that compare") resolves against the
# immediately preceding turn or two in practice; 5 gives generous margin
# beyond that without letting a long-running conversation's prompt size
# (and therefore Generator's real per-call latency/cost) grow unbounded.
# Matches this project's established "ship minimal, measure before
# adding speculative complexity" pattern from reranking's rollout
# (.agent/MEMORY.md's 2026-07-27 reranking decision) rather than a
# token-budget scheme, which would need its own tokenizer call just to
# enforce and isn't obviously better-justified at this scale.
MAX_HISTORY_TURNS = 5


# FastAPI dependency-provider indirection, same pattern as
# routes/ingest.py's get_pipeline_runner() — real constructor injection
# via app.dependency_overrides in tests, not monkeypatching real classes.
def get_retriever() -> Retriever:
    return Retriever()


def get_generator() -> Generator:
    return Generator()


def get_verifier() -> Verifier:
    return Verifier()


def _extract_claim_spans(answer: str, cited_positions: set[int]) -> dict[int, str]:
    """Maps each cited position (1-indexed, from GenerateResult.cited_indices)
    to the claim text that supports it — the sentence it appears in, with
    every `[...]` citation bracket stripped out. If a position is cited
    from more than one sentence, the FIRST occurrence's sentence is used
    (a repeated citation doesn't need independent re-verification).

    2026-07-24 full-flow audit (item 4) confirmed nothing existed to
    reuse for this — built fresh here. Deliberately lightweight: sentence
    boundaries, not real claim/discourse understanding, are enough
    structure to hand Verifier a focused span per citation rather than
    the entire answer every time. Reuses generator.py's own
    CITATION_BRACKET/CITATION_NUMBER (not a second, independently
    maintained regex) so this can never silently disagree with
    Generator's own citation parsing on what counts as a marker.
    """
    spans: dict[int, str] = {}
    for sentence in _SENTENCE_BOUNDARY.split(answer):
        positions_in_sentence: set[int] = set()
        for bracket in CITATION_BRACKET.finditer(sentence):
            for match in CITATION_NUMBER.finditer(bracket.group(1)):
                positions_in_sentence.add(int(match.group()))

        clean_sentence = CITATION_BRACKET.sub("", sentence)
        clean_sentence = re.sub(r"\s+([.,;:!?])", r"\1", clean_sentence)  # space left before punctuation by a removed bracket
        clean_sentence = re.sub(r"\s+", " ", clean_sentence).strip()

        for position in positions_in_sentence:
            if position in cited_positions and position not in spans and clean_sentence:
                spans[position] = clean_sentence
    return spans


def _strip_dropped_markers(answer: str, dropped_positions: set[int]) -> str:
    """Rewrites `[...]` brackets to remove only the dropped positions —
    NOT a naive per-marker string replace, since Gemini has been observed
    live grouping multiple citations into one bracket (FEAT-010's
    self-audit: `[2, 3]`, `[1, 2, 4]`). A bracket with a mix of kept and
    dropped positions keeps only the kept ones (`[1, 2]` with 2 dropped
    -> `[1]`); a bracket with nothing left is removed entirely, along
    with any stray space this leaves before punctuation."""

    def rebuild(match: re.Match) -> str:
        content = match.group(1)
        numbers = [int(m.group()) for m in CITATION_NUMBER.finditer(content)]
        if not numbers:
            return match.group(0)  # not a citation-shaped bracket at all — leave untouched
        kept = [n for n in numbers if n not in dropped_positions]
        if not kept:
            return ""
        return "[" + ", ".join(str(n) for n in kept) + "]"

    stripped = CITATION_BRACKET.sub(rebuild, answer)
    stripped = re.sub(r"\s+([.,;:!?])", r"\1", stripped)  # space left before punctuation by a removed bracket
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    return stripped.strip()


@router.post("/query", response_model=QueryResponse, response_model_exclude_none=True)
async def post_query(
    payload: QueryRequest,
    request: Request,
    retriever: Retriever = Depends(get_retriever),
    generator: Generator = Depends(get_generator),
    verifier: Verifier = Depends(get_verifier),
):
    # request.state.user_id (FEAT-003, JWT-verified) is THE tenant
    # boundary for this entire endpoint — the 2026-07-24 full-flow audit
    # (item 1) found Generator/Verifier have no user_id concept at all
    # downstream of Retriever, so this is the ONLY place a wrong user_id
    # could ever enter the pipeline. QueryRequest has no user_id field of
    # its own (models/query.py) — there is no request-body value that
    # could be used instead, by construction, not just by convention.
    user_id = request.state.user_id
    client = get_service_role_client()

    if not payload.document_ids:
        return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "document_ids must not be empty"))
    if not payload.question.strip():
        return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "question must not be empty"))

    requested_ids = set(payload.document_ids)
    owned_ids = queries.documents_owned_by_user(client, document_ids=payload.document_ids, user_id=user_id)
    if requested_ids - owned_ids:
        # Identical response whether a document_id doesn't exist at all
        # or belongs to another user — same discipline as
        # get_document()'s 404 (API_CONTRACT.md), just a 403 here per
        # this endpoint's own documented contract.
        return JSONResponse(status_code=403, content=error_envelope("FORBIDDEN", "one or more document_ids do not belong to the authenticated user"))

    prior_messages: list[dict] = []
    if payload.conversation_id is not None:
        conversation = queries.get_conversation(client, conversation_id=payload.conversation_id, user_id=user_id)
        if conversation is None:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "conversation not found"))
        # Minimal conversation memory (2026-07-27): prior turns fold into
        # Generator's prompt so a follow-up like "how does that compare
        # to X?" resolves correctly in the ANSWER. Reuses FEAT-026's
        # already-isolation-proven fetch (conversation_id + user_id both
        # filtered) rather than a second, parallel history path — the
        # only change is the new `limit` param, which this is the first
        # caller to use. Retrieval below is UNCHANGED: it still searches
        # using only payload.question, no query rewriting — a deliberate
        # scope decision (.agent/FEATURES.md), not an oversight.
        prior_messages = queries.list_messages_for_conversation(
            client, conversation_id=payload.conversation_id, user_id=user_id, limit=MAX_HISTORY_TURNS * 2
        )

    started = time.perf_counter()

    retrieved = retriever.retrieve(payload.question, payload.document_ids, user_id, k=payload.k)

    if not retrieved:
        # A legitimate, benign outcome (no matching content) — not an
        # error. Nothing to generate from, so Gemini is never called.
        answer_text = "I couldn't find relevant information in the selected documents to answer this question."
        latency_ms = int((time.perf_counter() - started) * 1000)
        persisted = queries.create_query_turn(
            client,
            user_id=user_id,
            conversation_id=payload.conversation_id,
            document_ids=payload.document_ids,
            question=payload.question,
            answer_content=answer_text,
            answer_raw_content=answer_text,
            retrieved_chunk_ids=[],
            answer_metadata={"model": None, "input_tokens": 0, "output_tokens": 0, "latency_ms": latency_ms},
            citations=[],
        )
        return QueryResponse(
            conversation_id=persisted["conversation_id"],
            message_id=persisted["message_id"],
            answer=answer_text,
            citations=[],
            metadata=QueryMetadata(
                model="none", verifier_model="none", retrieved_count=0, cited_count=0, latency_ms=latency_ms
            ),
        )

    # RetrievedChunk carries no image data (FEAT-009's RPC functions
    # never SELECT figure_path) — fetch_generator_chunks() is the
    # figure-image fetch the 2026-07-24 full-flow audit (item 3)
    # confirmed did not exist anywhere yet.
    generator_chunks = fetch_generator_chunks(client, retrieved)

    try:
        gen_result = generator.generate(payload.question, generator_chunks, history=prior_messages)
    except GenerationError as exc:
        logger.error("post_query: generation failed for user %s: %s", user_id, exc)
        return JSONResponse(status_code=502, content=error_envelope("GENERATE_FAILED", "answer generation failed"))

    # Generator.generate() returns 1-indexed POSITIONS into `chunks`, not
    # chunk ids (FEAT-010's own acceptance criteria) — this route is the
    # first production caller, and this mapping is explicitly its job,
    # not Generator's. chunks[N-1] is the same list, same order, used to
    # build generator_chunks above.
    cited_positions = set(gen_result.cited_indices)
    claim_spans = _extract_claim_spans(gen_result.answer, cited_positions)

    verify_pairs: list[tuple[str, GeneratorChunk]] = []
    verify_positions: list[int] = []
    for position in gen_result.cited_indices:
        claim_text = claim_spans.get(position)
        if claim_text:
            verify_pairs.append((claim_text, generator_chunks[position - 1]))
            verify_positions.append(position)

    verdicts: list[Verdict] = verifier.verify_batch(verify_pairs)

    # verdict == UNSUPPORTED (including a Verifier-internal failure,
    # which Verifier itself already forces to UNSUPPORTED — never
    # retried, never silently upgraded) is dropped: not returned to the
    # client, its marker stripped from the answer text. SUPPORTED and
    # PARTIAL are both kept — PARTIAL renders with a warning indicator
    # client-side (ARCHITECTURE.md's verify flow; API_CONTRACT.md now
    # documents this explicitly after the 2026-07-24 full-flow audit
    # found it was previously undocumented there).
    dropped_positions: set[int] = set()
    citation_responses: list[CitationResponse] = []
    citations_to_persist: list[dict] = []

    retrieved_by_chunk_id = {r.chunk_id: r for r in retrieved}

    for position, verdict in zip(verify_positions, verdicts, strict=True):
        chunk = generator_chunks[position - 1]
        retrieved_chunk = retrieved_by_chunk_id[chunk.chunk_id]
        claim_text = claim_spans[position]

        citations_to_persist.append(
            {
                "chunk_id": chunk.chunk_id,
                "marker": position,
                "claim_span": claim_text,
                "claim_start": None,
                "claim_end": None,
                "verdict": verdict.verdict.value,
                "supporting_quote": verdict.quote,
                "verifier_model": verdict.model,
            }
        )

        if verdict.verdict == VerdictLabel.UNSUPPORTED:
            dropped_positions.add(position)
            continue

        # figure_path is only set on GeneratorChunk when figure_fetcher.py's
        # download actually succeeded (FEAT-026) — reuses that resolution
        # rather than a second chunks.select("figure_path") lookup.
        figure_url = None
        if chunk.element_type == "figure" and chunk.figure_path:
            figure_url = signed_figure_url(client, chunk.figure_path)

        citation_responses.append(
            CitationResponse(
                marker=position,
                chunk_id=chunk.chunk_id,
                document_id=retrieved_chunk.document_id,
                document_name=chunk.document_name,
                page_number=chunk.page_number,
                element_type=chunk.element_type,
                snippet=chunk.content[:200],
                verdict=verdict.verdict.value,
                supporting_quote=verdict.quote,
                figure_url=figure_url,
            )
        )

    final_answer = _strip_dropped_markers(gen_result.answer, dropped_positions) if dropped_positions else gen_result.answer

    latency_ms = int((time.perf_counter() - started) * 1000)

    persisted = queries.create_query_turn(
        client,
        user_id=user_id,
        conversation_id=payload.conversation_id,
        document_ids=payload.document_ids,
        question=payload.question,
        answer_content=final_answer,
        answer_raw_content=gen_result.answer,
        retrieved_chunk_ids=[c.chunk_id for c in generator_chunks],
        answer_metadata={
            "model": gen_result.model,
            "input_tokens": gen_result.input_tokens,
            "output_tokens": gen_result.output_tokens,
            "latency_ms": latency_ms,
        },
        citations=citations_to_persist,
    )

    return QueryResponse(
        conversation_id=persisted["conversation_id"],
        message_id=persisted["message_id"],
        answer=final_answer,
        citations=citation_responses,
        metadata=QueryMetadata(
            model=gen_result.model,
            verifier_model=verdicts[0].model if verdicts else "none",
            retrieved_count=len(retrieved),
            cited_count=len(citation_responses),
            latency_ms=latency_ms,
        ),
    )
