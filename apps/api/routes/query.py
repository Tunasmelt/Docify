import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.query import CitationResponse, QueryMetadata, QueryRequest, QueryResponse
from rate_limit import limiter
from services.figure_fetcher import fetch_generator_chunks, signed_figure_url
from services.generator import CITATION_BRACKET, CITATION_NUMBER, GenerationError, Generator, GeneratorChunk
from services.retriever import Retriever
from services.verifier import Verdict, VerdictLabel, Verifier

logger = logging.getLogger(__name__)

# FEAT-024 (2026-07-28) — real vendor ceilings, not round numbers:
#
# Per-minute (Voyage): every real call to either /query or /query/stream
# makes exactly one Voyage embed_query call (Retriever.retrieve(), no
# rerank by default per FEAT-009's own opt-in decision) — the identical
# 3 RPM shared free-tier ceiling /ingest's embed call draws on
# (MEMORY.md). 3/minute per user is looser than /ingest's 2/minute
# since asking questions is the core "try the demo" interaction real
# visitors repeat most — but it draws on the SAME shared pool, so it's
# still bounded, not generous.
#
# Per-day (Gemini): each real call also makes one gemini-3.6-flash
# generation call plus one gemini-3.5-flash-lite verification call per
# cited claim (parallelized, services/verifier.py) — a DIFFERENT quota
# bucket from /ingest's OCR-tier-1 gemini-2.5-flash 20/day ceiling
# (MEMORY.md's 2026-07-26 entry confirms these are per-model, separate
# buckets, not shared). Neither model's exact free-tier daily ceiling
# is published/confirmed (same entry) — 40/day per user is a
# deliberately generous-but-real ceiling: high enough not to interrupt
# a normal demo session, low enough that no single user's runaway
# script silently drives unbounded real API cost against an unverified
# limit.
#
# /query and /query/stream share ONE combined counter via
# limiter.shared_limit(scope=...) below, not two independent ones —
# they're the same underlying action (ask a question) with two
# different response-delivery mechanisms, and both draw on the
# identical real vendor calls above. Giving them separate counters
# would silently let a user double their real effective quota by
# alternating endpoints; confirmed live in test_rate_limit.py that a
# user split across both routes hits ONE shared limit, not two.
#
# Same stated limitation as /ingest's comment: this bounds each route
# (or, here, each shared action) per user, not a single global counter
# across /ingest and /query combined — see that file's comment for the
# full reasoning.
QUERY_MINUTE_LIMIT = "3/minute"
QUERY_DAY_LIMIT = "40/day"
QUERY_RATE_LIMIT_SCOPE = "query_action"

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


# 2026-08-02 — resolution cascade for _extract_claim_spans (Part 3,
# citation-recovery follow-up). Paragraphs are blank-line-separated
# blocks; a paragraph made ENTIRELY of markdown-style bullet/numbered
# lines is treated as a list, where the "enclosing paragraph" fallback
# below narrows to just the one list-item LINE the marker is actually in
# — a citation attached to one bullet is a claim about THAT bullet, not
# every other bullet in the same list.
_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
_LIST_ITEM_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _clean_claim_text(text: str) -> str:
    # A leading bullet/number marker is formatting, not claim content —
    # stripped first so a list item that's genuinely nothing but a
    # citation (e.g. "- [3].") cleans to truly empty ("") rather than a
    # residual "-." that would technically count as "non-empty" and
    # incorrectly short-circuit the cascade at this tier.
    text = _LIST_ITEM_PREFIX.sub("", text, count=1)
    clean = CITATION_BRACKET.sub("", text)
    clean = re.sub(r"\s+([.,;:!?])", r"\1", clean)  # space left before punctuation by a removed bracket
    clean = re.sub(r"\s+", " ", clean).strip()
    # A result with no real word/number characters left (e.g. a lone "."
    # or "-." after stripping a bullet marker and a bracket from a line
    # that was genuinely nothing but a citation) is not real claim text —
    # treated as empty so the cascade correctly moves on to the next
    # tier instead of "recovering" punctuation-only noise.
    if clean and not re.search(r"\w", clean):
        clean = ""
    return clean


def _bracketed_positions(text: str) -> set[int]:
    positions: set[int] = set()
    for bracket in CITATION_BRACKET.finditer(text):
        for match in CITATION_NUMBER.finditer(bracket.group(1)):
            positions.add(int(match.group()))
    return positions


def _is_list_paragraph(paragraph: str) -> bool:
    lines = [line for line in paragraph.split("\n") if line.strip()]
    return bool(lines) and all(_LIST_ITEM_PREFIX.match(line) for line in lines)


def _extract_claim_spans(answer: str, cited_positions: set[int]) -> dict[int, str]:
    """Maps each cited position (1-indexed, from GenerateResult.cited_indices)
    to the claim text that supports it, trying progressively wider spans
    of text until one actually has real content to hand the verifier:

    (a) the SENTENCE the marker appears in (the original, still-primary
        behavior — every `[...]` citation bracket stripped out).
    (b) if that sentence turns out to be empty once brackets are
        stripped (nothing left but the marker itself — a bare `[3].`
        with no attached prose), the enclosing PARAGRAPH — or, if that
        paragraph is a markdown-style list, just the one list-item LINE
        the marker is in, not every bullet in the list.
    (c) if the marker's own paragraph is ALSO empty of real text (a
        genuinely standalone/sentence-initial citation, isolated in its
        own paragraph with nothing else nearby), the PRECEDING sentence
        in the whole answer's own reading order — a trailing citation
        like "Also relevant: [3]" or a marker opening its own paragraph
        most often refers back to what was just said immediately before it.
    (d) only if all three come up empty is the position left unresolved
        (the caller drops it, same fail-safe behavior as before this
        cascade existed).

    If a position is cited from more than one sentence, the FIRST
    occurrence (in answer order) is used — a repeated citation doesn't
    need independent re-verification, unchanged from the original
    behavior.

    IMPORTANT — this does NOT weaken FEAT-011's verification guarantee:
    every claim text this returns, however it was resolved, still goes
    through the identical verify_batch() check every other citation
    does (routes/query.py's post_query/_stream_query_events, both
    unchanged by this cascade) — a citation "recovered" via the
    paragraph/preceding-sentence fallback is graded on the SAME
    supported/partial/unsupported scale as one resolved via tier (a),
    and is dropped exactly the same way if the verifier finds the wider
    span doesn't actually support the claim. Widening WHERE the claim
    text comes from never widens what counts as verified.

    2026-07-24 full-flow audit (item 4) confirmed nothing existed to
    reuse for this — built fresh here. Deliberately lightweight: sentence/
    paragraph boundaries, not real claim/discourse understanding, are
    enough structure to hand Verifier a focused span per citation rather
    than the entire answer every time. Reuses generator.py's own
    CITATION_BRACKET/CITATION_NUMBER (not a second, independently
    maintained regex) so this can never silently disagree with
    Generator's own citation parsing on what counts as a marker.
    """
    # Tier (a) — BYTE-IDENTICAL to the pre-cascade algorithm: a flat
    # sentence split over the WHOLE raw answer, with no paragraph
    # awareness at all. Deliberately kept exactly as it was (not folded
    # into the paragraph-aware structure tiers b/c use below) — an
    # earlier version of this cascade pre-split by paragraph FIRST, which
    # changed tier (a)'s own boundaries for some already-working inputs:
    # a paragraph with no terminal punctuation before a blank line (e.g.
    # a markdown header with no period) used to glue across that blank
    # line under the original flat splitter, sometimes recovering MORE
    # real content than a paragraph-first version would (verified live:
    # "## Summary\n\n[1]\n\nReal content." originally resolved to the
    # FULL glued text "## Summary Real content." under flat splitting;
    # a paragraph-first rewrite would have only recovered "## Summary"
    # for the same input via a later tier — strictly worse). Tiers (b)/(c)
    # are pure ADDITIONS reached only for positions tier (a) still can't
    # resolve, so no already-working case can regress.
    flat_sentences = _SENTENCE_BOUNDARY.split(answer)
    spans: dict[int, str] = {}
    first_sentence_index: dict[int, int] = {}
    for index, sentence in enumerate(flat_sentences):
        positions_in_sentence = _bracketed_positions(sentence) & cited_positions
        if not positions_in_sentence:
            continue
        tier_a = _clean_claim_text(sentence)
        for position in positions_in_sentence:
            first_sentence_index.setdefault(position, index)
            if position not in spans and tier_a:
                spans[position] = tier_a

    unresolved = [p for p in cited_positions if p not in spans and p in first_sentence_index]
    if not unresolved:
        return spans

    paragraphs = _PARAGRAPH_BOUNDARY.split(answer)
    for position in unresolved:
        owning_paragraph = next((p for p in paragraphs if position in _bracketed_positions(p)), None)

        tier_b = ""
        if owning_paragraph is not None:
            if _is_list_paragraph(owning_paragraph):
                item_line = next(
                    (line for line in owning_paragraph.split("\n") if line.strip() and position in _bracketed_positions(line)),
                    owning_paragraph,
                )
                tier_b = _clean_claim_text(item_line)
            else:
                tier_b = _clean_claim_text(owning_paragraph)

        # Reachable only when (a) AND (b) both came up empty — the
        # marker's own sentence AND its whole enclosing paragraph have
        # no real text of their own, i.e. it's genuinely sentence-
        # initial/standalone. Uses the SAME flat_sentences list tier (a)
        # did — "preceding" means in the whole answer's own flat reading
        # order, which can cross a paragraph boundary.
        tier_c = ""
        if not tier_b:
            index = first_sentence_index[position]
            if index > 0:
                tier_c = _clean_claim_text(flat_sentences[index - 1])

        claim = tier_b or tier_c
        if claim:
            spans[position] = claim
    return spans


_SENTENCE_WITH_SEPARATOR = re.compile(r"(?<=[.!?])(\s+)")
_LEADING_MARKERS = re.compile(r"^((?:\s*\[[^\[\]]*\])+)\s*")
_EMPTY_LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])?\s*$")


def _markers_in(text: str) -> list[int]:
    return [
        int(n.group())
        for bracket in CITATION_BRACKET.finditer(text)
        for n in CITATION_NUMBER.finditer(bracket.group(1))
    ]


def _has_prose(text: str) -> bool:
    return bool(re.sub(r"[\s.,;:!?]", "", CITATION_BRACKET.sub("", text)))


def _remove_unsupported_claims(answer: str, unsupported_positions: set[int]) -> tuple[str, int]:
    """Removes every sentence whose citations were ALL judged unsupported by
    the verifier — stripping only the [N] marker would leave the rejected
    claim in the answer, now looking like ordinary uncited text. A sentence
    that also cites a kept source stays (the kept source supports it); its
    unsupported marker is stripped later by _strip_dropped_markers. Works
    line by line so markdown lists keep their structure, and a marker-only
    segment after a full stop ("Claim. [2]") counts for the sentence before
    it. Returns (new_answer, number_of_sentences_removed)."""
    if not unsupported_positions:
        return answer, 0

    removed = 0
    out_lines: list[str] = []
    for line in answer.split("\n"):
        parts = _SENTENCE_WITH_SEPARATOR.split(line)
        # parts alternates [sentence, separator, sentence, ...]; markers at the
        # start of a segment are folded into the preceding sentence.
        sentences: list[list[str]] = []  # [text, trailing_separator]
        for i in range(0, len(parts), 2):
            text = parts[i]
            separator = parts[i + 1] if i + 1 < len(parts) else ""
            leading = _LEADING_MARKERS.match(text) if sentences else None
            if leading and _markers_in(leading.group(1)):
                # "Claim. [2] Next sentence" — the markers belong to "Claim."
                sentences[-1][0] += sentences[-1][1] + leading.group(1).strip()
                sentences[-1][1] = " "
                text = text[leading.end():]
                if not text.strip():
                    sentences[-1][1] = separator
                    continue
            sentences.append([text, separator])

        kept_parts: list[str] = []
        line_had_removal = False
        for text, separator in sentences:
            markers = _markers_in(text)
            if markers and _has_prose(text) and all(m in unsupported_positions for m in markers):
                removed += 1
                line_had_removal = True
                continue
            kept_parts.append(text + separator)
        new_line = "".join(kept_parts).rstrip()
        if line_had_removal and _EMPTY_LIST_ITEM.match(new_line):
            continue  # the whole line (e.g. a list item) was the unsupported claim
        out_lines.append(new_line if line_had_removal else line)

    return "\n".join(out_lines), removed


def _note_removed_claims(answer: str, removed: int) -> str:
    if not removed:
        return answer
    noun = "statement was" if removed == 1 else "statements were"
    note = f"_{removed} {noun} removed because the cited source did not support {'it' if removed == 1 else 'them'}._"
    return f"{answer.rstrip()}\n\n{note}" if answer.strip() else note


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


def _is_resolvable_marker(position, num_chunks: int) -> bool:
    """Defense-in-depth guard (2026-07-31), added after a real production
    incident: a streaming turn crashed persistence with "null value in
    column 'marker'... violates not-null constraint," rolling back the
    entire turn (message text included — create_query_turn is one atomic
    function) because one citation reached the DB insert with an
    unresolvable marker.

    Live reproduction (real Gemini calls, broad-summarization prompts,
    both single-turn and history-carrying follow-ups, plus hand-built
    adversarial answer shapes fed through the real parsing functions)
    never found a path where the CURRENT `_extract_claim_spans`+persist-
    loop logic actually produces this — every position in
    `cited_indices` is, by construction, a plain int already range-
    validated by `_parse_citations` (1..num_chunks), and a position with
    no resolvable claim-bearing sentence is already filtered out before
    `citations_to_persist.append()` is ever reached (see the `if
    claim_text:` gate below). But "not reproduced today" is not the same
    as "provably cannot happen," and citation-integrity bugs are exactly
    the class this project has adversarially audited hardest, precisely
    because they're silent, not crashing, until they aren't. This is the
    second, independent layer: even if some future change to Generator/
    claim-span extraction ever DID produce an unresolvable position, it
    gets dropped here — the same fail-safe treatment FEAT-010 already
    gives a hallucinated (out-of-range) marker — rather than ever
    reaching the DB. The SQL-side fix (migration
    20260731_002_citation_persistence_defensive.sql) is the other layer:
    even if a bad marker DID reach the DB, one bad citation can no
    longer take the whole turn down with it."""
    return isinstance(position, int) and not isinstance(position, bool) and 1 <= position <= num_chunks


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
@limiter.shared_limit(QUERY_MINUTE_LIMIT, scope=QUERY_RATE_LIMIT_SCOPE)
@limiter.shared_limit(QUERY_DAY_LIMIT, scope=QUERY_RATE_LIMIT_SCOPE)
def post_query(
    payload: QueryRequest,
    request: Request,
    response: Response,
    retriever: Retriever = Depends(get_retriever),
    generator: Generator = Depends(get_generator),
    verifier: Verifier = Depends(get_verifier),
):
    # `response` is never touched directly below — same reason as
    # routes/ingest.py's post_ingest: this route returns a plain
    # QueryResponse Pydantic model (via response_model=), not a raw
    # Response, so @limiter.limit(...)/shared_limit(...) below has
    # nothing to attach its rate-limit headers to without this
    # parameter. Confirmed live (2026-07-30) — post_query_stream does
    # NOT need this fix, since every path it returns (JSONResponse for
    # validation/ownership errors, StreamingResponse for the real
    # success path) is already a real Response instance.
    #
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

    retrieved = retriever.retrieve(payload.question, payload.document_ids, user_id, k=payload.k, rerank=payload.rerank)

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

    # Declared here, not after verify_batch(), so BOTH loops below can add
    # to the same set — a position dropped for ANY reason (unresolvable
    # marker, no resolvable claim text, or a real UNSUPPORTED verdict)
    # must have its [N] stripped from the final answer text the same way.
    # Previously only the UNSUPPORTED case did this; a citation dropped by
    # either guard below left a dangling, unclickable [N] visible in the
    # answer with no matching citation object — a real, separate (non-
    # data-loss) bug found while adding these guards, fixed here too.
    dropped_positions: set[int] = set()
    unsupported_positions: set[int] = set()

    verify_pairs: list[tuple[str, GeneratorChunk]] = []
    verify_positions: list[int] = []
    for position in gen_result.cited_indices:
        # Guards `generator_chunks[position - 1]` below, not just the
        # later DB-insert loop — a real Generator's cited_indices is
        # already range-validated against len(chunks) by
        # Generator._parse_citations, so this should never fire given
        # the current pipeline, but an out-of-range position reaching
        # this line would otherwise raise an uncaught IndexError (this
        # route has no surrounding try/except past this point, unlike
        # the streaming path). See _is_resolvable_marker's docstring.
        if not _is_resolvable_marker(position, len(generator_chunks)):
            logger.warning(
                "post_query: dropping an unresolvable citation position (%r) for user %s before verification",
                position,
                user_id,
            )
            dropped_positions.add(position)
            continue
        claim_text = claim_spans.get(position)
        if claim_text:
            verify_pairs.append((claim_text, generator_chunks[position - 1]))
            verify_positions.append(position)
        else:
            # No resolvable claim-bearing sentence anywhere in the
            # answer for this position (e.g. a trailing standalone [N]
            # after the final sentence) — never verified, never
            # persisted, same as before; the difference is its marker
            # now gets stripped too instead of dangling in the answer.
            dropped_positions.add(position)

    verdicts: list[Verdict] = verifier.verify_batch(verify_pairs)

    # verdict == UNSUPPORTED — a real model judgment that the claim is
    # false, or a caught fabricated/ungrounded quote (Verifier still
    # forces these to UNSUPPORTED — never retried, never silently
    # upgraded) — is dropped: not returned to the client, its marker
    # stripped from the answer text. SUPPORTED, PARTIAL, and UNVERIFIED
    # (2026-08-03 — verification genuinely could not run: a Verifier-
    # internal infrastructure failure, distinct from a real UNSUPPORTED
    # verdict) are all kept. This check is deliberately an equality
    # check against UNSUPPORTED specifically, not an allowlist of
    # "keep" states — a new verdict label that isn't literally
    # UNSUPPORTED falls through to kept/shown automatically. PARTIAL and
    # UNVERIFIED each render with their own distinct client-side
    # indicator (ARCHITECTURE.md's verify flow; API_CONTRACT.md documents
    # all four states explicitly).
    citation_responses: list[CitationResponse] = []
    citations_to_persist: list[dict] = []

    retrieved_by_chunk_id = {r.chunk_id: r for r in retrieved}

    for position, verdict in zip(verify_positions, verdicts, strict=True):
        if not _is_resolvable_marker(position, len(generator_chunks)):
            # See _is_resolvable_marker's docstring — defense-in-depth,
            # never expected to fire given the current upstream logic
            # (verify_positions only ever contains positions that
            # already passed the identical check above), but a citation
            # that can't resolve to a real marker must fail the same
            # safe way a hallucinated marker already does, not reach
            # the DB insert at all.
            logger.warning(
                "post_query: dropping a citation with an unresolvable marker (%r) for user %s — "
                "never persisted, never returned to the client",
                position,
                user_id,
            )
            dropped_positions.add(position)
            continue

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
            unsupported_positions.add(position)
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
                association_method=retrieved_chunk.association_method,
                snippet=chunk.content[:200],
                verdict=verdict.verdict.value,
                supporting_quote=verdict.quote,
                figure_url=figure_url,
            )
        )

    pruned_answer, removed_claims = _remove_unsupported_claims(gen_result.answer, unsupported_positions)
    final_answer = _strip_dropped_markers(pruned_answer, dropped_positions) if dropped_positions else pruned_answer
    final_answer = _note_removed_claims(final_answer, removed_claims)

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
    # Standard `event: <type>\ndata: {json}\n\n` SSE framing. `data` is
    # always a single JSON object per event — never multi-line/raw text —
    # so the frontend parser only has one shape to handle regardless of
    # event type.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# 2026-08-02 (FEAT-016 follow-up) — heartbeat + client-disconnect handling.
#
# HEARTBEAT: a real gap of several real seconds can open up between two
# domain events — retrieval, the wait for Gemini's own first streamed
# token, and a slow verify_batch() call are all real, unbounded-duration
# waits with no `yield` of their own in between. A reverse proxy/load
# balancer sitting in front of this app (nginx, a cloud LB) can have its
# own idle-connection timeout well under what a slow real call can take,
# and would silently kill the connection mid-wait with nothing to stop
# it. ": keepalive\n\n" is the standard SSE COMMENT syntax (RFC-shaped:
# any line starting with ":" is a comment, ignored by spec-compliant SSE
# parsers) — sent periodically while a real stage is still pending, never
# as its own named event.
#
# Verified directly against THIS project's actual client (2026-08-02) —
# not assumed from generic EventSource behavior, since apps/web/lib/api/
# query.ts (FEAT-016) deliberately does NOT use the browser's EventSource
# (it can't send a POST body/Authorization header) and instead hand-rolls
# SSE parsing via fetch()+ReadableStream, splitting on "\n\n" and looking
# for "event:"/"data:" line prefixes (dispatchSseFrame). A ": keepalive"
# frame has neither prefix, so `dataLines` stays empty and
# dispatchSseFrame returns immediately without dispatching anything —
# confirmed by reading that exact function, not by assuming spec
# compliance transfers to a hand-rolled parser.
_HEARTBEAT_INTERVAL_S = 12
_KEEPALIVE_FRAME = ": keepalive\n\n"


async def _yield_heartbeats_until_done(task: asyncio.Task):
    """Yields `_KEEPALIVE_FRAME` every `_HEARTBEAT_INTERVAL_S` seconds
    while `task` is still pending; yields nothing and returns the moment
    it completes. The caller retrieves the real result/exception via
    `task.result()` afterward (synchronous once `task.done()` — no
    further await needed) — this generator's only job is the wire-level
    keepalive framing during the wait, never the task's own outcome."""
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_INTERVAL_S)
        if not done:
            yield _KEEPALIVE_FRAME


# CLIENT DISCONNECT: real, live-server investigation (2026-08-02, not
# simulated) confirmed the pre-existing behavior: NOTHING in this
# function ever checked for a disconnected client, at any point. A
# genuine client disconnect (a real TCP close, driven by an actual
# httpx connection closed mid-stream against a real uvicorn server) made
# during retrieval still let retrieval run to completion, THEN started
# generation, THEN started verification — the full pipeline, uninterrupted,
# for a stream nobody was reading. Root cause: Starlette's modern
# StreamingResponse (ASGI spec >= 2.4) only discovers a dead connection
# REACTIVELY, when it next tries to physically write a chunk (`send()`
# raising OSError) — there is no separate task racing a disconnect signal
# against the body iterator the way older Starlette versions had. Between
# two yields, while this generator is suspended awaiting a slow
# `asyncio.to_thread(...)` call, nothing is polling for a disconnect at
# all, so a stage that was ALREADY running when the client left always
# runs to completion regardless (an in-flight Python thread cannot be
# force-cancelled) — but nothing stops the NEXT stage from starting
# needlessly.
#
# `request.is_disconnected()` (FastAPI/Starlette's own documented API for
# exactly this) does NOT work here — confirmed live, not assumed: a real
# uvicorn server + a real httpx connection genuinely closed mid-stream
# (`response.close()`, no connection pooling) left `is_disconnected()`
# returning `False` for the full 3+ real seconds a slow retrieval step
# kept running, even though the client-side socket was confirmably
# closed the entire time. Root cause: `is_disconnected()`'s own
# implementation wraps its `receive()` call in an immediately-cancelled
# `anyio.CancelScope` (a non-blocking "peek") — but uvicorn's ASGI
# receive-channel only ever gets a real `http.disconnect` message
# delivered to a `receive()` call that's GENUINELY, continuously pending
# when the transport's own `connection_lost` fires; a call that cancels
# itself before that can happen never receives it. Confirmed directly:
# an ACTIVELY-awaited `request.receive()` loop (raced against real work
# via `asyncio.wait`, `_watch_for_disconnect` below), NOT `is_disconnected()`,
# detected the identical real disconnect in ~20ms. `is_disconnected()` is
# not used anywhere in this file because of this — a real, generalizable
# lesson logged in `.agent/MEMORY.md`.
#
# `_watch_for_disconnect` runs as one long-lived background task for the
# whole stream, setting `disconnected` (an `asyncio.Event`) the moment a
# real `http.disconnect` message arrives. `_abort_if_disconnected` below
# is then a trivial, synchronous check of that Event — called before
# generation and before verification (the two real, unbounded, quota-
# costing stages) so neither ever starts for a request nobody can see the
# result of. It does not stop whichever stage was ALREADY running at the
# moment of disconnect (an in-flight Python thread cannot be force-
# cancelled regardless of how fast the disconnect itself is detected).
#
# DECISION — an aborted turn is DISCARDED, never persisted as partial:
# matches this project's existing all-or-nothing persistence model
# (create_query_turn is one atomic call, made only once at the very end;
# there was never a "partial turn" concept to begin with) and SCOPE.md's
# established no-partial-data discipline for ingestion. A turn nobody
# will ever see the confirmation of is not worth inventing a new partial-
# persistence path for.
async def _watch_for_disconnect(request: Request, disconnected: asyncio.Event) -> None:
    """Runs for the lifetime of one /query/stream call (spawned once at
    the top of _stream_query_events, cancelled in its `finally`). Sets
    `disconnected` the moment a real ASGI `http.disconnect` message
    arrives — see the module comment above for why this, and not
    `request.is_disconnected()`, is the mechanism confirmed to work."""
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            disconnected.set()
            return


def _abort_if_disconnected(disconnected: asyncio.Event, stage: str, user_id: str) -> bool:
    if disconnected.is_set():
        logger.info(
            "post_query_stream: client disconnected before %s for user %s — aborting, nothing persisted",
            stage,
            user_id,
        )
        return True
    return False


async def _stream_query_events(
    payload: QueryRequest,
    user_id: str,
    client,
    retriever: Retriever,
    generator: Generator,
    verifier: Verifier,
    prior_messages: list[dict],
    disconnected: asyncio.Event,
):
    """The actual SSE body for POST /query/stream (FEAT-016). Emits, in
    order: `retrieving` -> `token` (one per Gemini text delta, zero or
    more) -> `verifying` -> `citations-resolved` -> `done`. An `error`
    event can replace any step from that point on and always ends the
    stream — there is no path that closes the connection without either
    a `done` or an `error`, so the frontend never has to guess whether a
    silent disconnect means success or failure. `: keepalive\n\n` SSE
    comment frames (never a named event) can appear between any two of
    the above during a real, slow gap — see _yield_heartbeats_until_done.

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
        # in the first place. Also wrapped with a heartbeat (2026-08-02)
        # — real retrieval latency is unbounded (a real Postgres/Voyage
        # call), and this is the very first potentially-long gap in the
        # whole stream, right after only one small event has gone out.
        retrieve_task = asyncio.ensure_future(
            asyncio.to_thread(retriever.retrieve, payload.question, payload.document_ids, user_id, k=payload.k, rerank=payload.rerank)
        )
        async for heartbeat in _yield_heartbeats_until_done(retrieve_task):
            yield heartbeat
        retrieved = retrieve_task.result()
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

    # Real, live-server-confirmed gap (2026-08-02, see module comment
    # above _abort_if_disconnected): generation is the first of the two
    # genuinely expensive, quota-costing stages left to run. If the
    # client is already known to be gone, skip it entirely rather than
    # starting a real Gemini generation call nobody will ever see the
    # output of.
    if _abort_if_disconnected(disconnected, "generation", user_id):
        return

    generator_chunks = await asyncio.to_thread(fetch_generator_chunks, client, retrieved)

    # Heartbeat wraps EVERY step of the token stream (not just the first),
    # via the underlying async generator's own __anext__() — covers both
    # the real gap before Gemini's first token (retrieval+chunk-fetch
    # already happened above, so this is purely "waiting on Gemini to
    # start responding") and any real gap between individual token deltas.
    final_result = None
    try:
        stream_iter = generator.generate_stream(payload.question, generator_chunks, history=prior_messages).__aiter__()
        while True:
            next_task = asyncio.ensure_future(stream_iter.__anext__())
            async for heartbeat in _yield_heartbeats_until_done(next_task):
                yield heartbeat
            try:
                item = next_task.result()
            except StopAsyncIteration:
                break
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

    # Second real checkpoint: verification is the other genuinely
    # expensive stage (one real Gemini call per cited claim,
    # services/verifier.py) — if the client left while the answer was
    # still streaming (a very plausible real moment to close the tab,
    # once the visible text looks complete), skip it and the persistence
    # that would follow it.
    if _abort_if_disconnected(disconnected, "verification", user_id):
        return

    yield _sse("verifying", {})

    try:
        cited_positions = set(final_result.cited_indices)
        claim_spans = _extract_claim_spans(final_result.answer, cited_positions)

        # Declared here, not after verify_batch(), so BOTH loops below can
        # add to the same set — see post_query's identical comment. Same
        # dangling-marker fix applied identically to both paths.
        dropped_positions: set[int] = set()
        unsupported_positions: set[int] = set()

        verify_pairs: list[tuple[str, GeneratorChunk]] = []
        verify_positions: list[int] = []
        for position in final_result.cited_indices:
            # Same guard as post_query — see _is_resolvable_marker's
            # docstring. Here it's belt-and-suspenders (this whole block
            # is already inside the outer try/except below), but kept
            # identical to post_query's copy so the two paths can never
            # silently drift on which citations get dropped.
            if not _is_resolvable_marker(position, len(generator_chunks)):
                logger.warning(
                    "post_query_stream: dropping an unresolvable citation position (%r) for user %s before verification",
                    position,
                    user_id,
                )
                dropped_positions.add(position)
                continue
            claim_text = claim_spans.get(position)
            if claim_text:
                verify_pairs.append((claim_text, generator_chunks[position - 1]))
                verify_positions.append(position)
            else:
                dropped_positions.add(position)

        verify_task = asyncio.ensure_future(asyncio.to_thread(verifier.verify_batch, verify_pairs))
        async for heartbeat in _yield_heartbeats_until_done(verify_task):
            yield heartbeat
        verdicts: list[Verdict] = verify_task.result()

        citation_responses: list[CitationResponse] = []
        citations_to_persist: list[dict] = []
        retrieved_by_chunk_id = {r.chunk_id: r for r in retrieved}

        for position, verdict in zip(verify_positions, verdicts, strict=True):
            if not _is_resolvable_marker(position, len(generator_chunks)):
                # See _is_resolvable_marker's docstring — same
                # defense-in-depth guard as post_query, kept identical
                # between the streaming and non-streaming paths on
                # purpose (the one thing that must never drift between
                # them is which citations get dropped/kept).
                logger.warning(
                    "post_query_stream: dropping a citation with an unresolvable marker (%r) for user %s — "
                    "never persisted, never returned to the client",
                    position,
                    user_id,
                )
                dropped_positions.add(position)
                continue

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
                unsupported_positions.add(position)
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
                    association_method=retrieved_chunk.association_method,
                    snippet=chunk.content[:200],
                    verdict=verdict.verdict.value,
                    supporting_quote=verdict.quote,
                    figure_url=figure_url,
                )
            )

        pruned_answer, removed_claims = _remove_unsupported_claims(final_result.answer, unsupported_positions)
        final_answer = _strip_dropped_markers(pruned_answer, dropped_positions) if dropped_positions else pruned_answer
        final_answer = _note_removed_claims(final_answer, removed_claims)

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


async def _stream_query_events_with_disconnect_watch(
    payload: QueryRequest,
    user_id: str,
    client,
    retriever: Retriever,
    generator: Generator,
    verifier: Verifier,
    prior_messages: list[dict],
    request: Request,
):
    """Thin wrapper — owns the real disconnect-watcher task's lifecycle
    (spawned here, cancelled in `finally` on every exit path: success,
    error, or an aborted-early return) so `_stream_query_events` itself
    never has to be individually re-indented/wrapped at each of its many
    existing return points. `_stream_query_events` only ever sees the
    plain `asyncio.Event`, not `request` — it doesn't need to know HOW
    disconnection is detected, only whether it happened."""
    disconnected = asyncio.Event()
    watch_task = asyncio.ensure_future(_watch_for_disconnect(request, disconnected))
    try:
        async for frame in _stream_query_events(
            payload, user_id, client, retriever, generator, verifier, prior_messages, disconnected
        ):
            yield frame
    finally:
        watch_task.cancel()


@router.post("/query/stream")
@limiter.shared_limit(QUERY_MINUTE_LIMIT, scope=QUERY_RATE_LIMIT_SCOPE)
@limiter.shared_limit(QUERY_DAY_LIMIT, scope=QUERY_RATE_LIMIT_SCOPE)
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
    # Both are blocking Supabase calls — off the event loop, same as every
    # call inside the stream itself.
    error = await asyncio.to_thread(_check_ownership, client, payload, user_id)
    if error is not None:
        return error
    prior_messages, error = await asyncio.to_thread(_load_history, client, payload, user_id)
    if error is not None:
        return error

    return StreamingResponse(
        _stream_query_events_with_disconnect_watch(
            payload, user_id, client, retriever, generator, verifier, prior_messages, request
        ),
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
