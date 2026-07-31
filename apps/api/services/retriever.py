import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import voyageai
from voyageai.error import VoyageError

from db.client import get_service_role_client
from services.embedder import Embedder

logger = logging.getLogger(__name__)

# The Reciprocal Rank Fusion constant — NOT the retrieval `k` (how many
# results retrieve() returns). Deliberately named nothing like the other
# `k` anywhere in this module: RRF_K dampens the influence of low ranks in
# the fusion formula 1/(RRF_K + rank), and confusing it with the retrieval
# k silently produces a plausible-looking but wrong result (no error would
# ever surface it). Default per .agent/SCOPE.md / FEATURES.md.
RRF_K = 60

# Default number of final results retrieve() returns, per SCOPE.md's
# Phase 2 spec ("Top-k retrieval with configurable k (default 8)").
DEFAULT_K = 8

# Each search method (vector, FTS) fetches this many candidates *before*
# fusion, not just the final k — RRF can only promote a chunk that ranks,
# say, 15th in vector search but 2nd in FTS if both searches actually
# looked far enough to surface it. Fetching only the top-k from each
# method first would silently cut off exactly the chunks hybrid search
# exists to rescue. min 20 keeps the pool meaningful even for a small k.
_CANDIDATE_POOL_MULTIPLIER = 4
_MIN_CANDIDATE_POOL = 20

# Voyage's rerank model — verified live + against installed SDK source,
# .agent/api-docs/voyage.md. No default in the SDK; `rerank-2.5` is the
# current/latest cross-encoder reranker (as opposed to older rerank-2/
# rerank-1 families).
RERANK_MODEL = "rerank-2.5"
RERANK_MAX_RETRIES = 3

# How many of RRF's fused candidates reranking is given to reconsider —
# deliberately named and set apart from both DEFAULT_K/k (the final
# return count) and _candidate_pool_size (the search-side fetch depth
# above): reranking can only promote a chunk RRF ranked, say, 15th if
# it's actually handed that candidate, but there's no reason to feed it
# every search-side candidate either. Fixed at 20 rather than scaled with
# k — same "don't conflate two differently-purposed constants" discipline
# RRF_K/DEFAULT_K's own naming already exists to guard against.
RERANK_POOL_SIZE = 20


@dataclass
class RetrievedChunk:
    """A ranked search result — deliberately not chunker.Chunk, which
    represents an ingestion-time chunk before it has an id, a fused
    score, or a document name. Different concept, different type."""

    chunk_id: str
    content: str
    page: int
    document_id: str
    document_name: str
    # The source document's real mime_type (documents.mime_type) — added
    # FEAT-020, 2026-07-27, so the citation-display layer can tell a real
    # PDF page from a PPTX slide index or a DOCX/HTML page_number=1
    # sentinel (these two formats have no real page concept at all; see
    # .agent/SCHEMA.md's page_number note). Was never selected anywhere
    # between `documents.mime_type` (stored since FEAT-001) and the
    # client before this.
    document_mime_type: str
    element_type: str
    # Higher is more relevant. RRF's fused score by default; when
    # rerank=True succeeds, this is Voyage's rerank relevance_score
    # instead (a real calibrated relevance signal, not just "won the
    # fusion sum") — retrieve()'s caller never sees `chunk_id`/`content`/
    # etc. treat this differently either way, and nothing downstream
    # (routes/query.py, generator.py) currently reads .score at all, so
    # there's no compatibility concern in swapping its meaning per call.
    score: float


def _candidate_pool_size(k: int) -> int:
    return max(k * _CANDIDATE_POOL_MULTIPLIER, _MIN_CANDIDATE_POOL)


def _reciprocal_rank_fusion(ranked_lists: list[list[dict]], *, rrf_k: int) -> list[tuple[dict, float]]:
    """Standard RRF: score(chunk) = sum over every ranking it appears in
    of 1 / (rrf_k + rank_in_that_ranking), rank starting at 1. A chunk
    found by more rankings scores higher than one found by fewer — that's
    the entire mechanism "hybrid" search relies on here, not a weighted
    average or any bespoke logic. Returns (row, score) pairs sorted by
    score descending.

    Generalized (2026-07-31) from a fixed vector+FTS pair to N ranked
    lists: a mixed-provider scope needs one independent vector ranking
    PER embedding_provider present (a Voyage-space distance and a
    Gemini-space distance are not comparable to each other, but RANKS
    within each provider's own partition are meaningful — RRF only ever
    consumes rank, never raw score, which is exactly why this
    generalization is safe: it never compares a Voyage distance to a
    Gemini distance, only "how this chunk ranked within its own
    provider's search"). Callers pass [vector_results_provider_a,
    vector_results_provider_b, ..., fts_results] — order among the list
    doesn't matter, RRF treats every ranked list identically."""
    scores: dict[str, float] = {}
    rows_by_id: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, row in enumerate(ranked_list, start=1):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (rrf_k + rank)
            rows_by_id.setdefault(row["id"], row)

    ranked_ids = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [(rows_by_id[chunk_id], scores[chunk_id]) for chunk_id in ranked_ids]


def _default_rerank_client() -> voyageai.Client:
    return voyageai.Client(max_retries=RERANK_MAX_RETRIES)


class Reranker:
    """Thin wrapper around Voyage's rerank-2.5 cross-encoder. Constructing
    a Reranker never touches the network or reads VOYAGE_API_KEY — the
    real client is built lazily on first actual rerank() call, so a bare
    Retriever() (which builds a default Reranker) never crashes in an
    environment that never passes rerank=True, even if VOYAGE_API_KEY is
    absent. Same lazy-credential discipline FEAT-017's audit established
    for the OCR fallback tiers, applied here because voyageai.Client()
    itself was confirmed (2026-07-27, live check) to raise
    AuthenticationError immediately at construction if no key is present
    — an eager-construction crash of the exact same shape, just from a
    different SDK."""

    def __init__(self, client: voyageai.Client | None = None):
        self._client = client

    def _get_client(self) -> voyageai.Client:
        if self._client is None:
            self._client = _default_rerank_client()
        return self._client

    def rerank(
        self, query: str, candidates: list[tuple[dict, float]], k: int
    ) -> list[tuple[dict, float]] | None:
        """Reranks (row, _rrf_score) candidate pairs by real Voyage
        relevance against `query`, returning the top `k` as (row,
        relevance_score) pairs already in relevance order. Returns None
        on ANY failure (auth, quota, network, malformed response) —
        never raises, never returns an empty/partial result silently.
        The caller (Retriever.retrieve) must treat None as "fall back to
        RRF's own ranking", not as "no results"."""
        if not candidates:
            return []

        documents = [row["content"] for row, _rrf_score in candidates]
        try:
            client = self._get_client()
            result = client.rerank(query=query, documents=documents, model=RERANK_MODEL, top_k=k)
        except VoyageError as exc:
            logger.warning("Reranking failed (%s: %s) — falling back to RRF ranking", type(exc).__name__, exc)
            return None
        except Exception as exc:  # defense in depth: no SDK-provided guarantee response parsing can't raise
            logger.warning("Reranking failed unexpectedly (%s: %s) — falling back to RRF ranking", type(exc).__name__, exc)
            return None

        try:
            return [(candidates[r.index][0], r.relevance_score) for r in result.results]
        except (AttributeError, IndexError, TypeError) as exc:
            logger.warning("Reranking returned an unexpected shape (%s: %s) — falling back to RRF ranking", type(exc).__name__, exc)
            return None


class Retriever:
    def __init__(self, client=None, embedder=None, reranker=None):
        self._client = client or get_service_role_client()
        self._embedder = embedder or Embedder()
        self._reranker = reranker or Reranker()

    def retrieve(
        self,
        question: str,
        document_ids: list[str],
        user_id: str,
        k: int = DEFAULT_K,
        rerank: bool = False,
    ) -> list[RetrievedChunk]:
        """Hybrid retrieval: vector (cosine, pgvector) and BM25-lite
        (Postgres FTS) run concurrently on separate threads — both are
        independent read-only queries with no dependency on each other's
        results, so real thread-level parallelism is worthwhile here
        (unlike most of this codebase's request-handling code, which is
        synchronous-blocking end to end — see .agent/reviews/
        2026-07-23-efficiency.md). Merged via Reciprocal Rank Fusion.

        Mixed-provider scope (2026-07-31): a document_ids scope can
        contain chunks embedded by more than one provider (services/
        embedder.py's Gemini fallback tags only the batches that actually
        hit it). This method never compares a Voyage-space vector to a
        Gemini-space one — it runs one independent vector search PER
        distinct provider actually present (_distinct_providers_in_scope),
        each in its own thread, and RRF-fuses all of them together with
        FTS purely on RANK, never raw distance (see
        _reciprocal_rank_fusion). A provider with zero chunks in this
        scope never gets a query-embed call at all.

        user_id and document_ids are passed as explicit SQL function
        parameters and filtered in the WHERE clause of both
        match_chunks_by_vector/match_chunks_by_fts (see the migration
        that defines them) — not relied on via RLS alone. Both functions
        are only ever called with the service-role client, which
        bypasses RLS entirely, so this explicit scoping is the actual
        tenant boundary, matching FEAT-007/008's established pattern.

        rerank: opt-in, default off. FEAT-009's own real quality fixture
        found RRF alone lands the expected chunk in the top-5 4/4 times
        (2 at rank 1, 2 at rank 2) — per .agent/MEMORY.md's standing
        "measure before enabling" leaning on rerank, this stays
        caller-toggled rather than always-on until real evidence
        (this feature's own follow-up test) justifies flipping the
        default. When True, RRF's top RERANK_POOL_SIZE candidates are
        sent to Voyage's reranker and the real top-k comes back in
        Voyage's relevance order; any rerank failure (network, quota,
        auth) falls back to RRF's own top-k unchanged — reranking is
        strictly additive, never a new way for retrieve() to return
        worse-than-baseline or nothing.
        """
        if not document_ids:
            return []

        # Mixed-provider scope support (2026-07-31): which embedding
        # spaces are actually present determines which query-embed calls
        # get made — never speculatively embed the query into a provider's
        # space that has no chunks in this scope (real cost: an extra
        # Voyage/Gemini API call for a search that couldn't possibly
        # return anything from that space). Falls back to {"voyage"} for
        # an empty result (no chunks ingested for this scope yet) purely
        # so vector search still runs its normal "no results" path rather
        # than needing a special case.
        providers_in_scope = self._distinct_providers_in_scope(document_ids, user_id) or {"voyage"}
        pool_size = _candidate_pool_size(k)

        # Each provider's embed_query() call AND its vector_search() call
        # both happen inside the submitted task (_embed_and_search_for_provider),
        # not before pool.submit — embed_query is a real network call, and
        # submitting only the RPC search while calling embed_query()
        # eagerly on the main thread would silently serialize exactly the
        # calls this thread pool exists to parallelize.
        with ThreadPoolExecutor(max_workers=len(providers_in_scope) + 1) as pool:
            vector_futures = [
                pool.submit(self._embed_and_search_for_provider, question, document_ids, user_id, pool_size, provider)
                for provider in providers_in_scope
            ]
            fts_future = pool.submit(self._fts_search, question, document_ids, user_id, pool_size)
            vector_result_lists = [future.result() for future in vector_futures]
            fts_results = fts_future.result()

        fused = _reciprocal_rank_fusion([*vector_result_lists, fts_results], rrf_k=RRF_K)

        final = fused[:k]
        if rerank:
            candidate_pool = fused[: max(RERANK_POOL_SIZE, k)]
            reranked = self._reranker.rerank(question, candidate_pool, k)
            if reranked is not None:
                final = reranked

        return [
            RetrievedChunk(
                chunk_id=row["id"],
                content=row["content"],
                page=row["page_number"],
                document_id=row["document_id"],
                document_name=row["document_name"],
                document_mime_type=row["document_mime_type"],
                element_type=row["element_type"],
                score=score,
            )
            for row, score in final
        ]

    def _embed_and_search_for_provider(
        self, question: str, document_ids: list[str], user_id: str, limit: int, provider: str
    ) -> list[dict]:
        """Runs entirely inside one pool.submit task so the real network
        call (embed_query) is genuinely parallelized across providers,
        not serialized on the main thread before submission."""
        query_vector = self._embedder.embed_query(question, provider=provider)
        return self._vector_search(query_vector, document_ids, user_id, limit, provider)

    def _distinct_providers_in_scope(self, document_ids: list[str], user_id: str) -> set[str]:
        result = self._client.rpc(
            "distinct_embedding_providers",
            {"match_user_id": user_id, "match_document_ids": document_ids},
        ).execute()
        return {row["embedding_provider"] for row in result.data}

    def _vector_search(
        self, query_vector: list[float], document_ids: list[str], user_id: str, limit: int, provider: str
    ) -> list[dict]:
        result = self._client.rpc(
            "match_chunks_by_vector",
            {
                "query_embedding": query_vector,
                "match_user_id": user_id,
                "match_document_ids": document_ids,
                "match_limit": limit,
                "match_provider": provider,
            },
        ).execute()
        return result.data

    def _fts_search(self, question: str, document_ids: list[str], user_id: str, limit: int) -> list[dict]:
        result = self._client.rpc(
            "match_chunks_by_fts",
            {
                "query_text": question,
                "match_user_id": user_id,
                "match_document_ids": document_ids,
                "match_limit": limit,
            },
        ).execute()
        return result.data
