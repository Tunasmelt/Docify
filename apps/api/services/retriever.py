import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

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


@dataclass
class RetrievedChunk:
    """A ranked search result — deliberately not chunker.Chunk, which
    represents an ingestion-time chunk before it has an id, a fused
    score, or a document name. Different concept, different type."""

    chunk_id: str
    content: str
    page: int
    document_name: str
    element_type: str
    score: float  # the fused RRF score — higher is more relevant


def _candidate_pool_size(k: int) -> int:
    return max(k * _CANDIDATE_POOL_MULTIPLIER, _MIN_CANDIDATE_POOL)


def _reciprocal_rank_fusion(
    vector_results: list[dict], fts_results: list[dict], *, rrf_k: int
) -> list[tuple[dict, float]]:
    """Standard RRF: score(chunk) = sum over every ranking it appears in
    of 1 / (rrf_k + rank_in_that_ranking), rank starting at 1. A chunk
    found by both methods scores higher than one found by only one —
    that's the entire mechanism "hybrid" search relies on here, not a
    weighted average or any bespoke logic. Returns (row, score) pairs
    sorted by score descending."""
    scores: dict[str, float] = {}
    rows_by_id: dict[str, dict] = {}

    for rank, row in enumerate(vector_results, start=1):
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (rrf_k + rank)
        rows_by_id[row["id"]] = row

    for rank, row in enumerate(fts_results, start=1):
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (rrf_k + rank)
        rows_by_id.setdefault(row["id"], row)

    ranked_ids = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [(rows_by_id[chunk_id], scores[chunk_id]) for chunk_id in ranked_ids]


class Retriever:
    def __init__(self, client=None, embedder=None):
        self._client = client or get_service_role_client()
        self._embedder = embedder or Embedder()

    def retrieve(
        self, question: str, document_ids: list[str], user_id: str, k: int = DEFAULT_K
    ) -> list[RetrievedChunk]:
        """Hybrid retrieval: vector (cosine, pgvector) and BM25-lite
        (Postgres FTS) run concurrently on separate threads — both are
        independent read-only queries with no dependency on each other's
        results, so real thread-level parallelism is worthwhile here
        (unlike most of this codebase's request-handling code, which is
        synchronous-blocking end to end — see .agent/reviews/
        2026-07-23-efficiency.md). Merged via Reciprocal Rank Fusion.

        user_id and document_ids are passed as explicit SQL function
        parameters and filtered in the WHERE clause of both
        match_chunks_by_vector/match_chunks_by_fts (see the migration
        that defines them) — not relied on via RLS alone. Both functions
        are only ever called with the service-role client, which
        bypasses RLS entirely, so this explicit scoping is the actual
        tenant boundary, matching FEAT-007/008's established pattern.
        """
        if not document_ids:
            return []

        query_vector = self._embedder.embed_query(question)
        pool_size = _candidate_pool_size(k)

        with ThreadPoolExecutor(max_workers=2) as pool:
            vector_future = pool.submit(self._vector_search, query_vector, document_ids, user_id, pool_size)
            fts_future = pool.submit(self._fts_search, question, document_ids, user_id, pool_size)
            vector_results = vector_future.result()
            fts_results = fts_future.result()

        fused = _reciprocal_rank_fusion(vector_results, fts_results, rrf_k=RRF_K)

        return [
            RetrievedChunk(
                chunk_id=row["id"],
                content=row["content"],
                page=row["page_number"],
                document_name=row["document_name"],
                element_type=row["element_type"],
                score=score,
            )
            for row, score in fused[:k]
        ]

    def _vector_search(self, query_vector: list[float], document_ids: list[str], user_id: str, limit: int) -> list[dict]:
        result = self._client.rpc(
            "match_chunks_by_vector",
            {
                "query_embedding": query_vector,
                "match_user_id": user_id,
                "match_document_ids": document_ids,
                "match_limit": limit,
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
