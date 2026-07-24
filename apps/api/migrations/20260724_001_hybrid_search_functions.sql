-- 20260724_001_hybrid_search_functions.sql
--
-- FEAT-009 (retriever service) needs to rank chunks by pgvector cosine
-- distance (`embedding <=> query_embedding`) and by Postgres FTS rank
-- (`ts_rank(ts, query)`) — neither is expressible through PostgREST's REST
-- query builder (`.select()`/`.order()`/`.eq()` only support plain column
-- comparisons and a fixed operator set, not arbitrary SQL expressions in
-- ORDER BY). The standard, documented way to do pgvector/FTS ranking
-- through Supabase's REST layer is a Postgres function called via
-- `client.rpc(...)` — not raw SQL from app code (this project's app code
-- has never used raw SQL; see the 2026-07-23 security review's "SQL/query
-- injection surface is currently parameterized or query-builder based"
-- finding — these functions keep that true: every value below is a bound
-- function parameter, not string-interpolated SQL).
--
-- Both functions take `match_user_id`/`match_document_ids` as explicit
-- parameters and filter on them in the WHERE clause directly — per
-- FEAT-009's task: "scoped to document_ids AND user_id explicitly in the
-- query (not relying on RLS alone)", matching the same defense-in-depth
-- pattern FEAT-007/008 already use at the PostgREST-filter layer. These
-- functions are only ever called with the service-role client (which
-- bypasses RLS entirely), so this explicit filtering is the *actual*
-- tenant boundary here, not a redundant belt-and-suspenders on top of RLS.
--
-- EXECUTE is revoked from PUBLIC (Postgres's default is to grant it) and
-- granted only to service_role — anon/authenticated have no legitimate
-- reason to call these directly, matching this project's established
-- "don't leave default-open access sitting around unused" discipline
-- (see 20260722_002's GRANT-vs-RLS lesson).

create or replace function match_chunks_by_vector(
  query_embedding vector(1024),
  match_user_id uuid,
  match_document_ids uuid[],
  match_limit int
)
returns table (
  id uuid,
  document_id uuid,
  document_name text,
  chunk_index int,
  element_type element_type,
  page_number int,
  content text,
  distance float8
)
language sql stable
as $$
  select
    c.id,
    c.document_id,
    d.filename as document_name,
    c.chunk_index,
    c.element_type,
    c.page_number,
    c.content,
    c.embedding <=> query_embedding as distance
  from chunks c
  join documents d on d.id = c.document_id
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
  order by c.embedding <=> query_embedding
  limit match_limit;
$$;

create or replace function match_chunks_by_fts(
  query_text text,
  match_user_id uuid,
  match_document_ids uuid[],
  match_limit int
)
returns table (
  id uuid,
  document_id uuid,
  document_name text,
  chunk_index int,
  element_type element_type,
  page_number int,
  content text,
  rank float4
)
language sql stable
as $$
  select
    c.id,
    c.document_id,
    d.filename as document_name,
    c.chunk_index,
    c.element_type,
    c.page_number,
    c.content,
    ts_rank(c.ts, websearch_to_tsquery('english', query_text)) as rank
  from chunks c
  join documents d on d.id = c.document_id
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.ts @@ websearch_to_tsquery('english', query_text)
  order by rank desc
  limit match_limit;
$$;

revoke execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int) from public;
revoke execute on function match_chunks_by_fts(text, uuid, uuid[], int) from public;
grant execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int) to service_role;
grant execute on function match_chunks_by_fts(text, uuid, uuid[], int) to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop function if exists match_chunks_by_vector(vector(1024), uuid, uuid[], int);
-- drop function if exists match_chunks_by_fts(text, uuid, uuid[], int);
