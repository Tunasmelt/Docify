import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

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


def _validate_payload(payload: QueryRequest) -> JSONResponse | None:
    if not payload.document_ids:
        return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "document_ids must not be empty"))
    if not payload.question.strip():
        return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", "question must not be empty"))
    return None


def _check_ownership(client, payload: QueryRequest, user_id: str) -> JSONResponse | None:
    requested_ids = set(payload.document_ids)
    owned_ids = queries.documents_owned_by_user(client, document_ids=payload.document_ids, user_id=user_id)
    if requested_ids - owned_ids:
        # Identical response whether a document_id doesn't exist at all
        # or belongs to another user — same discipline as
        # get_document()'s 404 (API_CONTRACT.md), just a 403 here per
        # this endpoint's own documented contract.
        return JSONResponse(status_code=403, content=error_envelope("FORBIDDEN", "one or more document_ids do not belong to the authenticated user"))
    return None


def _load_history(client, payload: QueryRequest, user_id: str) -> tuple[list[dict], JSONResponse | None]:
    if payload.conversation_id is None:
        return [], None
    conversation = queries.get_conversation(client, conversation_id=payload.conversation_id, user_id=user_id)
    if conversation is None:
        return [], JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "conversation not found"))
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
    return prior_messages, None


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

    error = _validate_payload(payload)
    if error is not None:
        return error
    error = _check_ownership(client, payload, user_id)
    if error is not None:
        return error
    prior_messages, error = _load_history(client, payload, user_id)
    if error is not None:
        return error

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
                document_mime_type=retrieved_chunk.document_mime_type,
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


def _sse(event: str, data: dict) -> str:
    # Standard `event: <type>\ndata: <json>\n\n` SSE framing. `data` is
    # always a single JSON object per event — never multi-line/raw text —
    # so the frontend parser only has one shape to handle regardless of
    # event type.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_query_events(
    payload: QueryRequest,
    user_id: str,
    client,
    retriever: Retriever,
    generator: Generator,
    verifier: Verifier,
    prior_messages: list[dict],
):
    """The actual SSE body for POST /query/stream (FEAT-016). Emits, in
    order: `retrieving` -> `token` (one per Gemini text delta, zero or
    more) -> `verifying` -> `citations-resolved` -> `done`. An `error`
    event can replace any step from that point on and always ends the
    stream — there is no path that closes the connection without either
    a `done` or an `error`, so the frontend never has to guess whether a
    silent disconnect means success or failure.

    Deliberately mirrors post_query()'s logic step-for-step (same
    citation-verdict handling, same claim-span extraction, same
    persistence call) rather than being a second, independently
    maintained pipeline — the one thing that must NEVER drift between
    the streaming and non-streaming paths is which citations get
    dropped/kept, since that's this project's core safety property.
    """
    started = time.perf_counter()
    yield _sse("retrieving", {})

    try:
        # asyncio.to_thread, not a direct call: Retriever/Verifier/the DB
        # client are all synchronous, blocking code (confirmed live —
        # calling verify_batch() directly here froze the event loop for
        # its entire ~8s real Gemini-call duration, which meant uvicorn
        # never got a chance to actually flush the already-yielded
        # `verifying` SSE frame to the socket until the NEXT yield
        # happened, so the client received `verifying` and
        # `citations-resolved` simultaneously instead of with the real
        # gap between them the whole point of a separate event was to
        # surface). Every blocking call in this function is wrapped the
        # same way from here down, not just this one, since an async
        # generator that blocks the loop for seconds at a time defeats
        # the entire purpose of streaming Gemini's tokens asynchronously
        # in the first place.
        retrieved = await asyncio.to_thread(retriever.retrieve, payload.question, payload.document_ids, user_id, k=payload.k)
    except Exception as exc:
        logger.error("post_query_stream: retrieval failed for user %s: %s", user_id, exc)
        yield _sse("error", {"code": "RETRIEVE_FAILED", "message": "retrieval failed"})
        return

    if not retrieved:
        # Same benign no-match outcome as post_query() — nothing to
        # generate from, so Gemini is never called and there is nothing
        # to stream.
        answer_text = "I couldn't find relevant information in the selected documents to answer this question."
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            persisted = await asyncio.to_thread(
                queries.create_query_turn,
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
        except Exception as exc:
            logger.error("post_query_stream: persistence failed (no-match case) for user %s: %s", user_id, exc)
            yield _sse("error", {"code": "PERSIST_FAILED", "message": "failed to save conversation turn"})
            return
        yield _sse(
            "citations-resolved",
            {
                "conversation_id": persisted["conversation_id"],
                "message_id": persisted["message_id"],
                "answer": answer_text,
                "citations": [],
            },
        )
        yield _sse(
            "done",
            {
                "metadata": {
                    "model": "none",
                    "verifier_model": "none",
                    "retrieved_count": 0,
                    "cited_count": 0,
                    "latency_ms": latency_ms,
                }
            },
        )
        return

    generator_chunks = await asyncio.to_thread(fetch_generator_chunks, client, retrieved)

    final_result = None
    try:
        async for item in generator.generate_stream(payload.question, generator_chunks, history=prior_messages):
            if isinstance(item, str):
                yield _sse("token", {"text": item})
            else:
                final_result = item
    except GenerationError as exc:
        logger.error("post_query_stream: generation failed for user %s: %s", user_id, exc)
        yield _sse("error", {"code": "GENERATE_FAILED", "message": "answer generation failed"})
        return

    # generate_stream() always either yields exactly one GenerateStreamResult
    # as its last item or raises GenerationError — reaching here with
    # final_result still None would mean that contract broke.
    if final_result is None:
        logger.error("post_query_stream: generate_stream ended with no final result for user %s", user_id)
        yield _sse("error", {"code": "GENERATE_FAILED", "message": "answer generation failed"})
        return

    yield _sse("verifying", {})

    try:
        cited_positions = set(final_result.cited_indices)
        claim_spans = _extract_claim_spans(final_result.answer, cited_positions)

        verify_pairs: list[tuple[str, GeneratorChunk]] = []
        verify_positions: list[int] = []
        for position in final_result.cited_indices:
            claim_text = claim_spans.get(position)
            if claim_text:
                verify_pairs.append((claim_text, generator_chunks[position - 1]))
                verify_positions.append(position)

        verdicts: list[Verdict] = await asyncio.to_thread(verifier.verify_batch, verify_pairs)

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

            figure_url = None
            if chunk.element_type == "figure" and chunk.figure_path:
                figure_url = await asyncio.to_thread(signed_figure_url, client, chunk.figure_path)

            citation_responses.append(
                CitationResponse(
                    marker=position,
                    chunk_id=chunk.chunk_id,
                    document_id=retrieved_chunk.document_id,
                    document_name=chunk.document_name,
                    document_mime_type=retrieved_chunk.document_mime_type,
                    page_number=chunk.page_number,
                    element_type=chunk.element_type,
                    snippet=chunk.content[:200],
                    verdict=verdict.verdict.value,
                    supporting_quote=verdict.quote,
                    figure_url=figure_url,
                )
            )

        final_answer = (
            _strip_dropped_markers(final_result.answer, dropped_positions) if dropped_positions else final_result.answer
        )

        latency_ms = int((time.perf_counter() - started) * 1000)

        persisted = await asyncio.to_thread(
            queries.create_query_turn,
            client,
            user_id=user_id,
            conversation_id=payload.conversation_id,
            document_ids=payload.document_ids,
            question=payload.question,
            answer_content=final_answer,
            answer_raw_content=final_result.answer,
            retrieved_chunk_ids=[c.chunk_id for c in generator_chunks],
            answer_metadata={
                "model": final_result.model,
                "input_tokens": final_result.input_tokens,
                "output_tokens": final_result.output_tokens,
                "latency_ms": latency_ms,
            },
            citations=citations_to_persist,
        )
    except Exception as exc:
        # Anything from here down (verification, citation building,
        # persistence) failing must never leave the client mid-stream
        # with no explanation — this is exactly the "verification fails
        # after streaming completes but before the stream closes" case
        # the task brief calls out explicitly.
        logger.error("post_query_stream: verification/persistence failed for user %s: %s", user_id, exc)
        yield _sse("error", {"code": "VERIFY_FAILED", "message": "citation verification failed"})
        return

    yield _sse(
        "citations-resolved",
        {
            "conversation_id": persisted["conversation_id"],
            "message_id": persisted["message_id"],
            "answer": final_answer,
            "citations": [c.model_dump(exclude_none=True) for c in citation_responses],
        },
    )

    yield _sse(
        "done",
        {
            "metadata": {
                "model": final_result.model,
                "verifier_model": verdicts[0].model if verdicts else "none",
                "retrieved_count": len(retrieved),
                "cited_count": len(citation_responses),
                "latency_ms": latency_ms,
            }
        },
    )


@router.post("/query/stream")
async def post_query_stream(
    payload: QueryRequest,
    request: Request,
    retriever: Retriever = Depends(get_retriever),
    generator: Generator = Depends(get_generator),
    verifier: Verifier = Depends(get_verifier),
):
    """SSE variant of POST /query (FEAT-016) — same auth/ownership/history
    validation, run to completion BEFORE the StreamingResponse is even
    constructed, so an invalid document_id, a 403, or a 404 conversation
    always comes back as a normal JSON error response, never as a
    stream that starts and then errors out. Kept as a separate route
    rather than a mode flag on /query: response_model=QueryResponse
    validation and a StreamingResponse are mutually exclusive in FastAPI,
    and every existing non-browser caller of /query (test_query_e2e.py,
    any future API integration) keeps its stable synchronous contract
    completely untouched.
    """
    user_id = request.state.user_id
    client = get_service_role_client()

    error = _validate_payload(payload)
    if error is not None:
        return error
    error = _check_ownership(client, payload, user_id)
    if error is not None:
        return error
    prior_messages, error = _load_history(client, payload, user_id)
    if error is not None:
        return error

    return StreamingResponse(
        _stream_query_events(payload, user_id, client, retriever, generator, verifier, prior_messages),
        media_type="text/event-stream",
        headers={
            # Nginx/other reverse proxies buffer SSE responses by default,
            # which would defeat progressive delivery entirely — this
            # header is the standard opt-out. Harmless locally (no proxy
            # in front of uvicorn in dev), but real for any deployed
            # environment (Phase 5).
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
