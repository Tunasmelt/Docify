# Tests for [FEAT-009] Retriever service (hybrid + RRF)
#
# Two distinct verification shapes, deliberately kept separate:
#
# 1. CORRECTNESS (fast, structural) — is the mechanism right: does RRF
#    fuse rankings correctly, does scoping actually exclude out-of-scope
#    chunks, are the returned fields right. These tests insert chunk rows
#    directly with hand-crafted, precisely-controllable embeddings and a
#    fake query embedder — real local Postgres/pgvector/FTS, but no real
#    Docling/Voyage calls, matching FEAT-006's own testing pattern.
#
# 2. QUALITY (slow, real) — does it find the actually-relevant content
#    for a real question against a real ingested document. This is the
#    part with no prior precedent in this project (FEAT-001 through
#    FEAT-008 never had to prove *relevance*, only correctness/safety),
#    and the part FEAT-012 will actually depend on being good. Gated
#    behind RUN_RETRIEVAL_QUALITY_TEST=1 (real Docling parse + real
#    Voyage calls — slow, costs quota), same pattern as
#    test_ingest_e2e.py. Run explicitly and reported, not just asserted.

import logging
import os
import time

import pytest
from voyageai.error import RateLimitError

from services.retriever import (
    DEFAULT_K,
    RERANK_MODEL,
    RERANK_POOL_SIZE,
    RRF_K,
    Reranker,
    RetrievedChunk,
    Retriever,
    _candidate_pool_size,
    _reciprocal_rank_fusion,
)


class FakeQueryEmbedder:
    """Returns a fixed, caller-controlled vector regardless of the query
    text — these tests are about retrieval mechanics, not semantic
    quality, so the actual embedding content doesn't matter, only that
    it's precisely controllable."""

    def __init__(self, vector):
        self._vector = vector

    def embed_query(self, text: str):
        return self._vector


class FakeReranker:
    """Records every rerank() call (query, candidates, k) for wiring/
    cost-guard assertions. With no rerank_fn injected, returns None on
    every call — doubling as both "reranker not wired at all" (calls
    stays empty) and "reranker was attempted but failed" (calls records
    it, return value simulates a real Voyage failure) depending on which
    the test cares about."""

    def __init__(self, rerank_fn=None):
        self._rerank_fn = rerank_fn
        self.calls = []

    def rerank(self, query, candidates, k):
        self.calls.append((query, candidates, k))
        if self._rerank_fn is None:
            return None
        return self._rerank_fn(query, candidates, k)


class _FakeRerankResult:
    def __init__(self, index, relevance_score):
        self.index = index
        self.relevance_score = relevance_score


class _FakeRerankResponse:
    def __init__(self, results):
        self.results = results


class _FakeVoyageRerankClient:
    """Stands in for voyageai.Client for Reranker-level unit tests — no
    real network call, no real API key needed."""

    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception
        self.calls = []

    def rerank(self, query, documents, model, top_k=None, truncation=True):
        self.calls.append({"query": query, "documents": documents, "model": model, "top_k": top_k})
        if self._exception is not None:
            raise self._exception
        return self._response


def _vector_along_dimension(dim_index: int, size: int = 1024) -> list[float]:
    """A unit-ish vector pointing along one axis. Two vectors built this
    way with the *same* dim_index are parallel (cosine distance ~0);
    different dim_index values are orthogonal (cosine distance ~1) —
    gives precise, easily-reasoned-about relative distances without
    needing real embeddings."""
    vector = [0.0] * size
    vector[dim_index] = 1.0
    return vector


def _create_document(admin, user_id: str, filename: str) -> str:
    return (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": filename,
                "storage_path": f"uploads/{user_id}/{filename}",
                "mime_type": "application/pdf",
                "size_bytes": 17,
            }
        )
        .execute()
        .data[0]["id"]
    )


def _insert_chunk(
    admin,
    *,
    document_id: str,
    user_id: str,
    chunk_index: int,
    content: str,
    embedding: list[float],
    page_number: int = 1,
    element_type: str = "text",
) -> str:
    return (
        admin.table("chunks")
        .insert(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk_index,
                "element_type": element_type,
                "page_number": page_number,
                "content": content,
                "embedding": embedding,
            }
        )
        .execute()
        .data[0]["id"]
    )


# --- Part 1: correctness (fast, structural) ---------------------------------


def test_merges_via_reciprocal_rank_fusion_k_60_default():
    assert RRF_K == 60
    assert DEFAULT_K == 8

    # A clean disagreement case: vector search's own #1 pick and FTS's
    # own #1 pick each individually lose to a chunk that ranked #2 (not
    # #1) in *both* — this is the entire point of hybrid fusion: neither
    # single method's top choice wins once the other method is factored
    # in, because "decent in both" outscores "best in only one" under RRF.
    vector_results = [
        {"id": "vector-only-top", "content": "v1"},
        {"id": "balanced", "content": "v2"},
    ]
    fts_results = [
        {"id": "fts-only-top", "content": "f1"},
        {"id": "balanced", "content": "f2"},
    ]

    fused = _reciprocal_rank_fusion(vector_results, fts_results, rrf_k=60)
    fused_ids = [row["id"] for row, _score in fused]

    assert fused_ids[0] == "balanced"
    assert set(fused_ids[1:]) == {"vector-only-top", "fts-only-top"}

    expected_balanced_score = 1 / 62 + 1 / 62
    expected_single_score = 1 / 61
    assert fused[0][1] == pytest.approx(expected_balanced_score)
    assert fused[1][1] == pytest.approx(expected_single_score)
    assert fused[2][1] == pytest.approx(expected_single_score)


def test_reciprocal_rank_fusion_handles_a_chunk_found_by_only_one_method():
    # A chunk vector search never surfaces at all (e.g. FTS-only.pdf's
    # literal keyword match with a poor embedding) must still make it
    # through fusion with a real, non-zero score — RRF must not require
    # presence in both rankings, only reward it.
    vector_results = []
    fts_results = [{"id": "fts-only", "content": "x"}]

    fused = _reciprocal_rank_fusion(vector_results, fts_results, rrf_k=60)

    assert len(fused) == 1
    assert fused[0][0]["id"] == "fts-only"
    assert fused[0][1] == pytest.approx(1 / 61)


# Acceptance criterion: `Retriever.retrieve(question, document_ids, user_id, k) -> list[Chunk]`
def test_retriever_retrieve_question_document_ids_user_id_k_list_chun(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "structural.pdf")
    _insert_chunk(
        admin,
        document_id=document_id,
        user_id=user_id,
        chunk_index=0,
        content="some retrievable content",
        embedding=_vector_along_dimension(0),
    )

    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(_vector_along_dimension(0)))
    results = retriever.retrieve("some question", [document_id], user_id, k=5)

    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], RetrievedChunk)


# Acceptance criterion: Runs vector search (cosine) and BM25 (Postgres FTS) in parallel
def test_runs_vector_search_cosine_and_bm25_postgres_fts_in_parallel(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "timing.pdf")

    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(_vector_along_dimension(0)))

    sleep_seconds = 0.4

    def slow_vector_search(*args, **kwargs):
        time.sleep(sleep_seconds)
        return []

    def slow_fts_search(*args, **kwargs):
        time.sleep(sleep_seconds)
        return []

    retriever._vector_search = slow_vector_search
    retriever._fts_search = slow_fts_search

    started = time.monotonic()
    retriever.retrieve("question", [document_id], user_id, k=5)
    elapsed = time.monotonic() - started

    # Sequential would take >= 2 * sleep_seconds (0.8s); real concurrency
    # keeps it close to a single sleep_seconds (0.4s) plus overhead.
    assert elapsed < sleep_seconds * 1.5, f"expected concurrent execution (~{sleep_seconds}s), took {elapsed:.3f}s"


# 2026-07-24 self-audit item 5: a partial failure in one of the two
# concurrent searches must never silently return results from only the
# other method with no signal anything went wrong — confirmed live for
# both directions (vector fails/FTS fails) before this was written as a
# permanent test. `Future.result()` re-raises the worker-thread exception
# on the calling thread; this locks that behavior in as a regression test
# rather than leaving it as a one-off manual check.
@pytest.mark.parametrize("broken_method", ["_vector_search", "_fts_search"])
def test_a_failing_search_method_raises_rather_than_silently_returning_partial_results(admin, user_a, broken_method):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "partial-failure.pdf")

    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(_vector_along_dimension(0)))

    def broken(*args, **kwargs):
        raise RuntimeError(f"simulated {broken_method} outage")

    setattr(retriever, broken_method, broken)

    with pytest.raises(RuntimeError, match=f"simulated {broken_method} outage"):
        retriever.retrieve("question", [document_id], user_id, k=5)


# Acceptance criterion: Returns top-k with metadata: chunk_id, content, page, document_name, element_type
def test_returns_top_k_with_metadata_chunk_id_content_page_document_n(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "metadata-check.pdf")

    chunk_id = _insert_chunk(
        admin,
        document_id=document_id,
        user_id=user_id,
        chunk_index=0,
        content="the exact content that should round-trip",
        embedding=_vector_along_dimension(0),
        page_number=7,
        element_type="table",
    )
    # A second, unrelated chunk (orthogonal embedding, no lexical overlap)
    # to confirm top-k actually limits, not just "returns everything".
    _insert_chunk(
        admin,
        document_id=document_id,
        user_id=user_id,
        chunk_index=1,
        content="completely unrelated filler text about nothing in particular",
        embedding=_vector_along_dimension(1),
    )

    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(_vector_along_dimension(0)))
    results = retriever.retrieve("question", [document_id], user_id, k=1)

    assert len(results) == 1
    result = results[0]
    assert result.chunk_id == chunk_id
    assert result.content == "the exact content that should round-trip"
    assert result.page == 7
    assert result.document_name == "metadata-check.pdf"
    assert result.element_type == "table"
    assert isinstance(result.score, float) and result.score > 0


# Acceptance criterion: user_id is included in every SQL WHERE clause explicitly
def test_user_id_is_included_in_every_sql_where_clause_explicitly(admin, user_a):
    """Direct check that both RPC calls actually pass match_user_id —
    complements (not replaces) the full behavioral isolation test below,
    since that's the only way to observe *why* isolation holds rather
    than just that it does."""
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "param-check.pdf")

    captured_params = []
    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(_vector_along_dimension(0)))

    original_rpc = admin.rpc

    def spying_rpc(func_name, params):
        captured_params.append((func_name, params))
        return original_rpc(func_name, params)

    admin.rpc = spying_rpc
    try:
        retriever.retrieve("question", [document_id], user_id, k=5)
    finally:
        admin.rpc = original_rpc

    assert len(captured_params) == 2
    for func_name, params in captured_params:
        assert params["match_user_id"] == user_id, f"{func_name} call did not receive match_user_id"
        assert params["match_document_ids"] == [document_id], f"{func_name} call did not receive match_document_ids"


# Multi-tenant isolation, task item 5 — same live-verification discipline
# as FEAT-007/008: real user B's chunks must never appear in real user
# A's retrieval results, even when B's content would otherwise
# semantically/lexically match the query.
def test_multi_tenant_isolation_excludes_other_users_chunks(admin, user_a, user_b):
    user_id_a, _token_a = user_a
    user_id_b, _token_b = user_b

    document_id_a = _create_document(admin, user_id_a, "user-a-doc.pdf")
    document_id_b = _create_document(admin, user_id_b, "user-b-doc.pdf")

    # Identical content and identical (maximally-matching) embedding —
    # if scoping didn't work, user B's chunk would tie or beat user A's.
    shared_vector = _vector_along_dimension(0)
    chunk_a_id = _insert_chunk(
        admin,
        document_id=document_id_a,
        user_id=user_id_a,
        chunk_index=0,
        content="the confidential quarterly revenue figure",
        embedding=shared_vector,
    )
    _insert_chunk(
        admin,
        document_id=document_id_b,
        user_id=user_id_b,
        chunk_index=0,
        content="the confidential quarterly revenue figure",
        embedding=shared_vector,
    )

    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(shared_vector))
    results = retriever.retrieve(
        "what is the confidential quarterly revenue figure", [document_id_a], user_id_a, k=10
    )

    assert len(results) == 1
    assert results[0].chunk_id == chunk_a_id
    assert all(r.document_name != "user-b-doc.pdf" for r in results)

    # Positive control: user B can retrieve their own, identical-looking
    # chunk when scoped to their own document/user_id — proves the
    # absence above is real scoping, not a broken query returning nothing
    # for anyone.
    results_as_b = retriever.retrieve(
        "what is the confidential quarterly revenue figure", [document_id_b], user_id_b, k=10
    )
    assert len(results_as_b) == 1
    assert results_as_b[0].document_name == "user-b-doc.pdf"


# Also confirms document_ids scoping specifically (not just user_id): a
# second document owned by the SAME user must still be excluded when not
# named in document_ids.
def test_document_ids_scoping_excludes_the_same_users_other_documents(admin, user_a):
    user_id, _token = user_a
    included_doc = _create_document(admin, user_id, "included.pdf")
    excluded_doc = _create_document(admin, user_id, "excluded.pdf")

    vector = _vector_along_dimension(0)
    included_chunk_id = _insert_chunk(
        admin, document_id=included_doc, user_id=user_id, chunk_index=0, content="target content", embedding=vector
    )
    _insert_chunk(
        admin, document_id=excluded_doc, user_id=user_id, chunk_index=0, content="target content", embedding=vector
    )

    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(vector))
    results = retriever.retrieve("target content", [included_doc], user_id, k=10)

    assert len(results) == 1
    assert results[0].chunk_id == included_chunk_id


# --- Reranking (FEAT-009 follow-up): Reranker unit tests --------------------


def test_reranker_maps_relevance_scores_back_to_original_rows_in_returned_order():
    row_a = {"id": "a", "content": "alpha"}
    row_b = {"id": "b", "content": "beta"}
    # RRF scores deliberately opposite of the rerank order below, so the
    # assertion can only pass if Reranker actually used Voyage's order,
    # not silently kept RRF's.
    candidates = [(row_a, 0.01), (row_b, 0.02)]

    fake_client = _FakeVoyageRerankClient(
        response=_FakeRerankResponse(
            [
                _FakeRerankResult(index=1, relevance_score=0.9),  # row_b, Voyage's #1 pick
                _FakeRerankResult(index=0, relevance_score=0.3),
            ]
        )
    )
    reranker = Reranker(client=fake_client)

    result = reranker.rerank("question", candidates, k=2)

    assert result == [(row_b, 0.9), (row_a, 0.3)]
    call = fake_client.calls[0]
    assert call["documents"] == ["alpha", "beta"]
    assert call["model"] == RERANK_MODEL
    assert call["top_k"] == 2


def test_reranker_returns_empty_list_immediately_for_empty_candidates():
    fake_client = _FakeVoyageRerankClient()
    reranker = Reranker(client=fake_client)

    result = reranker.rerank("question", [], k=5)

    assert result == []
    assert fake_client.calls == []


def test_reranker_returns_none_on_voyage_error_not_a_crash(caplog):
    fake_client = _FakeVoyageRerankClient(exception=RateLimitError("simulated rate limit"))
    reranker = Reranker(client=fake_client)

    with caplog.at_level(logging.WARNING):
        result = reranker.rerank("question", [({"id": "a", "content": "x"}, 0.1)], k=1)

    assert result is None
    assert "falling back to RRF ranking" in caplog.text


def test_reranker_returns_none_on_malformed_response_shape_not_a_crash(caplog):
    class _ResponseWithNoResultsAttribute:
        pass

    fake_client = _FakeVoyageRerankClient(response=_ResponseWithNoResultsAttribute())
    reranker = Reranker(client=fake_client)

    with caplog.at_level(logging.WARNING):
        result = reranker.rerank("question", [({"id": "a", "content": "x"}, 0.1)], k=1)

    assert result is None
    assert "falling back to RRF ranking" in caplog.text


def test_reranker_construction_never_touches_network_or_requires_api_key(monkeypatch):
    # Same lazy-credential discipline as FEAT-017's OCR tiers: constructing
    # a Reranker must not eagerly build a real voyageai.Client (which was
    # confirmed live to raise AuthenticationError immediately if
    # VOYAGE_API_KEY is absent) — only an actual rerank() call may do that.
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    Reranker()  # must not raise


# --- Reranking (FEAT-009 follow-up): Retriever.retrieve(rerank=...) wiring ---


def test_rerank_is_opt_in_reranker_never_called_when_rerank_not_requested(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "rerank-off.pdf")
    _insert_chunk(
        admin,
        document_id=document_id,
        user_id=user_id,
        chunk_index=0,
        content="some retrievable content",
        embedding=_vector_along_dimension(0),
    )

    fake_reranker = FakeReranker()
    retriever = Retriever(
        client=admin, embedder=FakeQueryEmbedder(_vector_along_dimension(0)), reranker=fake_reranker
    )
    retriever.retrieve("some question", [document_id], user_id, k=5)  # rerank defaults to False

    assert fake_reranker.calls == [], "reranker must not be invoked unless rerank=True is explicitly passed"


def test_rerank_true_uses_the_rerankers_result_ordering_when_it_succeeds(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "rerank-order.pdf")
    vector = _vector_along_dimension(0)
    _insert_chunk(
        admin, document_id=document_id, user_id=user_id, chunk_index=0, content="alpha content", embedding=vector
    )
    _insert_chunk(
        admin, document_id=document_id, user_id=user_id, chunk_index=1, content="beta content", embedding=vector
    )

    # Reverses whatever RRF handed it — reuses the exact real row dicts
    # the retriever passed in, so this is agnostic to Postgres's own
    # tie-breaking order between the two identically-embedded chunks.
    def reversing_rerank_fn(query, candidates, k):
        return list(reversed(candidates))[:k]

    fake_reranker = FakeReranker(rerank_fn=reversing_rerank_fn)
    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(vector), reranker=fake_reranker)

    results = retriever.retrieve("question", [document_id], user_id, k=2, rerank=True)

    assert len(fake_reranker.calls) == 1
    _query, candidates_received, k_received = fake_reranker.calls[0]
    assert k_received == 2
    expected_order = [row["id"] for row, _score in reversed(candidates_received)]
    assert [r.chunk_id for r in results] == expected_order


def test_rerank_true_falls_back_to_rrf_ranking_when_reranker_fails(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "rerank-fallback.pdf")
    vector = _vector_along_dimension(0)
    _insert_chunk(
        admin, document_id=document_id, user_id=user_id, chunk_index=0, content="alpha content", embedding=vector
    )
    _insert_chunk(
        admin, document_id=document_id, user_id=user_id, chunk_index=1, content="beta content", embedding=vector
    )

    fake_reranker = FakeReranker()  # no rerank_fn -> returns None, simulating a real Voyage failure

    retriever_rerank_on = Retriever(client=admin, embedder=FakeQueryEmbedder(vector), reranker=fake_reranker)
    retriever_rerank_off = Retriever(client=admin, embedder=FakeQueryEmbedder(vector))

    results_with_failed_rerank = retriever_rerank_on.retrieve(
        "question", [document_id], user_id, k=5, rerank=True
    )
    results_baseline = retriever_rerank_off.retrieve("question", [document_id], user_id, k=5, rerank=False)

    assert len(fake_reranker.calls) == 1, "rerank must have been attempted, not skipped"
    assert [r.chunk_id for r in results_with_failed_rerank] == [r.chunk_id for r in results_baseline]


def test_rerank_true_sends_the_rerank_pool_not_just_the_final_k(admin, user_a):
    user_id, _token = user_a
    document_id = _create_document(admin, user_id, "rerank-pool.pdf")
    vector = _vector_along_dimension(0)
    for i in range(3):
        _insert_chunk(
            admin, document_id=document_id, user_id=user_id, chunk_index=i, content=f"content {i}", embedding=vector
        )

    fake_reranker = FakeReranker(rerank_fn=lambda query, candidates, k: candidates[:k])
    retriever = Retriever(client=admin, embedder=FakeQueryEmbedder(vector), reranker=fake_reranker)

    retriever.retrieve("question", [document_id], user_id, k=2, rerank=True)

    _query, candidates_received, k_received = fake_reranker.calls[0]
    assert k_received == 2
    # All 3 fused candidates are handed to the reranker, not just the
    # final k=2 — reranking can only promote a lower-ranked chunk if it's
    # actually given the chance to see it.
    assert len(candidates_received) == 3
    assert RERANK_POOL_SIZE == 20  # documents this test's "give it more than k" premise


# --- Part 2: quality (slow, real) --------------------------------------------

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

QUALITY_QUESTIONS = [
    {
        "question": "What is Angola's Human Development Index value in 2010?",
        "expect_substring": "Angola",
        "why": "Table 19 (HDI) has an Angola row with value 4.42 in both 2000 and 2010 columns",
    },
    {
        "question": "Does Respondent C have a driving licence?",
        "expect_substring": "driving licence",
        "why": "Table 14 has a literal 'Do you have a driving licence?' row; Respondent C = Yes",
    },
    {
        "question": "What was the balance in the 2011 accounts?",
        "expect_substring": "Balance",
        "why": "Table 18 (accounts, 2011) ends in a 'Balance | Balance | 30,000' row",
    },
    {
        "question": "What courses does Institution X offer in Mathematics?",
        "expect_substring": "Mathematics",
        "why": "Table 15 (courses offered by Institution X) has a Mathematics row across 2006-2009",
    },
]


@pytest.mark.skipif(
    os.environ.get("RUN_RETRIEVAL_QUALITY_TEST") != "1",
    reason="set RUN_RETRIEVAL_QUALITY_TEST=1 to run real Docling+Voyage retrieval-quality checks (slow, uses Voyage quota)",
)
def test_retrieval_quality_against_real_table_heavy_pdf(admin, user_a):
    from PIL import Image  # noqa: F401  (not used directly, but confirms Pillow import path is fine under real ingest)
    from services.chunker import Chunker
    from services.embedder import Embedder
    from services.parser import Parser

    user_id, _token = user_a

    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parsed = Parser().parse(pdf_bytes)
    chunks = Chunker().chunk(parsed)
    embedder = Embedder()
    vectors = embedder.embed(chunks)

    document_id = _create_document(admin, user_id, "table_heavy.pdf")
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk.chunk_index,
                "element_type": chunk.element_type.value,
                "page_number": min(chunk.page_numbers),
                "content": chunk.content,
                "embedding": vector,
            }
        )
    admin.table("chunks").insert(rows).execute()

    for image in (c.image for c in chunks if c.image is not None):
        image.close()

    retriever = Retriever(client=admin)  # real Embedder, real embed_query() call per question

    # Voyage's free tier (no payment method on file) caps at 3 RPM —
    # confirmed empirically (this test hit real RateLimitErrors on its
    # 3rd query-embed call at 21s spacing, since the ingestion embed()
    # call just above already consumed one slot in the same rolling
    # window). Not a workaround for a bug in our code; a real account
    # constraint on this specific Voyage account. 25s margin, plus a
    # pause here before the first query, empirically holds.
    time.sleep(25)

    print("\n" + "=" * 90)
    print("FEAT-009 retrieval quality — real table_heavy.pdf, real Voyage embeddings")
    print("=" * 90)

    all_passed = True
    for i, spec in enumerate(QUALITY_QUESTIONS):
        if i > 0:
            time.sleep(25)
        results = retriever.retrieve(spec["question"], [document_id], user_id, k=5)

        print(f"\nQ: {spec['question']}")
        print(f"   (expecting a chunk containing {spec['expect_substring']!r} — {spec['why']})")
        for rank, r in enumerate(results, start=1):
            preview = r.content.replace("\n", " ")[:100]
            hit = spec["expect_substring"].lower() in r.content.lower()
            marker = " <-- MATCH" if hit else ""
            print(f"   [{rank}] score={r.score:.5f} page={r.page} {preview!r}{marker}")

        found_in_top_k = any(spec["expect_substring"].lower() in r.content.lower() for r in results)
        status = "PASS" if found_in_top_k else "FAIL"
        print(f"   -> {status}: expected substring {'found' if found_in_top_k else 'NOT found'} in top-5")
        all_passed = all_passed and found_in_top_k

    print("\n" + "=" * 90)
    print(f"Overall: {'ALL' if all_passed else 'NOT ALL'} questions retrieved their expected chunk in the top-5")
    print("=" * 90)

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()

    assert all_passed


@pytest.mark.skipif(
    os.environ.get("RUN_RETRIEVAL_QUALITY_TEST") != "1",
    reason="set RUN_RETRIEVAL_QUALITY_TEST=1 to run real Docling+Voyage rerank quality/latency checks (slow, uses Voyage quota)",
)
def test_reranking_effect_on_real_table_heavy_pdf_quality_questions(admin, user_a):
    """FEAT-009 rerank follow-up, task item 4/5: re-run the EXACT same 4
    quality questions above, paired same-run against both RRF-only and
    RRF+rerank, to report (a) whether reranking actually moves the
    expected chunk's rank and (b) reranking's own real added latency.

    Reuses the already-computed RRF-fused candidate pool for both the
    baseline and reranked comparison per question (embed_query() is
    called exactly once per question, matching FEAT-009's own original
    call budget) rather than calling retrieve() twice, which would
    double real Voyage embedding calls against this account's already-
    tight 3 RPM free-tier cap for no benefit — the baseline is byte-for-
    byte what retrieve(rerank=False) would return (identical fused[:k]
    slice), so nothing about the comparison's validity is lost.
    """
    from services.chunker import Chunker
    from services.embedder import Embedder
    from services.parser import Parser

    user_id, _token = user_a

    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parsed = Parser().parse(pdf_bytes)
    chunks = Chunker().chunk(parsed)
    embedder = Embedder()
    vectors = embedder.embed(chunks)

    document_id = _create_document(admin, user_id, "table_heavy_rerank.pdf")
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk.chunk_index,
                "element_type": chunk.element_type.value,
                "page_number": min(chunk.page_numbers),
                "content": chunk.content,
                "embedding": vector,
            }
        )
    admin.table("chunks").insert(rows).execute()

    for image in (c.image for c in chunks if c.image is not None):
        image.close()

    retriever = Retriever(client=admin)  # real Embedder, real Reranker

    # Same 3 RPM free-tier pacing FEAT-009's own quality test hit and
    # documented — this test makes 4 real embed_query calls (one per
    # question, same as the original) plus 4 real rerank calls (a
    # separate Voyage endpoint; not confirmed to share the same
    # rate-limit bucket, see .agent/api-docs/voyage.md's rerank section).
    time.sleep(25)

    k = 5
    print("\n" + "=" * 90)
    print("FEAT-009 rerank follow-up — real quality + latency comparison, table_heavy.pdf")
    print("=" * 90)

    rerank_latencies_ms = []
    summary_rows = []

    for i, spec in enumerate(QUALITY_QUESTIONS):
        if i > 0:
            time.sleep(25)

        query_vector = retriever._embedder.embed_query(spec["question"])
        pool_size = _candidate_pool_size(k)
        vector_results = retriever._vector_search(query_vector, [document_id], user_id, pool_size)
        fts_results = retriever._fts_search(spec["question"], [document_id], user_id, pool_size)
        fused = _reciprocal_rank_fusion(vector_results, fts_results, rrf_k=RRF_K)

        def rank_of_expected(rows_with_scores, substring=spec["expect_substring"]):
            return next(
                (rank for rank, (row, _score) in enumerate(rows_with_scores, start=1) if substring.lower() in row["content"].lower()),
                None,
            )

        baseline = fused[:k]
        baseline_rank = rank_of_expected(baseline)

        candidate_pool = fused[: max(RERANK_POOL_SIZE, k)]
        started = time.perf_counter()
        reranked = retriever._reranker.rerank(spec["question"], candidate_pool, k)
        rerank_latency_ms = (time.perf_counter() - started) * 1000
        rerank_latencies_ms.append(rerank_latency_ms)

        print(f"\nQ: {spec['question']}")
        print(f"   (expecting a chunk containing {spec['expect_substring']!r} — {spec['why']})")
        print(f"   rerank call latency: {rerank_latency_ms:.1f}ms")

        if reranked is None:
            print("   RERANK CALL FAILED (see WARNING log above) — reporting real degradation, not hiding it")
            reranked_rank = baseline_rank
            delta = "N/A (rerank call failed, fell back to RRF)"
        else:
            reranked_rank = rank_of_expected(reranked)
            if baseline_rank is None or reranked_rank is None:
                delta = "N/A (not found in candidate pool by one or both methods)"
            elif reranked_rank < baseline_rank:
                delta = "IMPROVED"
            elif reranked_rank > baseline_rank:
                delta = "WORSE"
            else:
                delta = "SAME"

        print(f"   baseline (RRF only) rank of expected chunk: {baseline_rank}")
        print(f"   reranked rank of expected chunk:            {reranked_rank}")
        print(f"   -> {delta}")
        summary_rows.append((spec["question"], baseline_rank, reranked_rank, delta))

    print("\n" + "=" * 90)
    print("Summary — baseline (RRF) rank vs reranked rank, real table_heavy.pdf:")
    for question, baseline_rank, reranked_rank, delta in summary_rows:
        print(f"  {delta:45s} baseline={baseline_rank} reranked={reranked_rank}  {question}")
    successful_latencies = [ms for ms in rerank_latencies_ms if ms is not None]
    if successful_latencies:
        print(
            f"\nReal rerank call latency — mean {sum(successful_latencies) / len(successful_latencies):.1f}ms, "
            f"min {min(successful_latencies):.1f}ms, max {max(successful_latencies):.1f}ms "
            f"(n={len(successful_latencies)} calls, k={k}, pool size <= {RERANK_POOL_SIZE})"
        )
    print("=" * 90)

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()
