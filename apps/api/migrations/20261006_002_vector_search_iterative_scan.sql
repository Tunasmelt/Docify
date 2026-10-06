-- 20261006_002_vector_search_iterative_scan.sql
--
-- pgvector's HNSW index returns the ~ef_search (default 40) nearest chunks
-- across ALL tenants first; the user_id / document_ids / provider filters run
-- afterwards. Once other users' chunks dominate a region of the embedding
-- space, a user's own nearest chunks fall outside that window and the search
-- returns few or none of them — reproduced locally: 400 of user B's chunks
-- near the query left user A with 0 of their 5 chunks. A match_limit above
-- ef_search was also silently capped at ef_search rows.
--
-- hnsw.iterative_scan (pgvector 0.8+) keeps scanning the graph until enough
-- rows pass the filters. strict_order keeps results in exact distance order.
-- Set on the function so it applies however the function is called; the
-- per-scan budget is bounded by hnsw.max_scan_tuples (default 20,000).
--
-- Same signature and RETURNS TABLE as 20260802_002, so CREATE OR REPLACE is
-- enough and existing grants carry over.

-- Using a vector value loads pgvector's library in this session, which
-- registers hnsw.* settings; without it, the SET clause below is rejected
-- ("permission denied to set parameter") for non-superuser roles.
select '[1]'::vector;

create or replace function match_chunks_by_vector(
  query_embedding vector(1024),
  match_user_id uuid,
  match_document_ids uuid[],
  match_limit int,
  match_provider embedding_provider
)
returns table (
  id uuid,
  document_id uuid,
  document_name text,
  document_mime_type text,
  chunk_index int,
  element_type element_type,
  page_number int,
  content text,
  association_method text,
  distance float8
)
language sql stable
-- Keep scanning the HNSW graph until match_limit rows survive the WHERE
-- filters (pgvector >= 0.8), in exact distance order.
set hnsw.iterative_scan = 'strict_order'
as $$
  select
    c.id,
    c.document_id,
    d.filename as document_name,
    d.mime_type as document_mime_type,
    c.chunk_index,
    c.element_type,
    c.page_number,
    c.content,
    c.metadata->>'association_method' as association_method,
    c.embedding <=> query_embedding as distance
  from chunks c
  join documents d on d.id = c.document_id
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.embedding_provider = match_provider
  order by c.embedding <=> query_embedding
  limit match_limit;
$$;


-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- Re-run the match_chunks_by_vector CREATE statement from
-- 20260802_002_citation_association_method.sql as CREATE OR REPLACE
-- (identical except for the `set hnsw.iterative_scan` line).
