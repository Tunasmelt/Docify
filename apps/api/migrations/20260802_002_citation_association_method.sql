-- 20260802_002_citation_association_method.sql
--
-- Surfaces chunks.metadata->>'association_method' (chunker.py's own
-- output — "explicit"/"heuristic"/"unmatched"/null, see services/
-- chunker.py's Chunk dataclass) through retrieval and into the citation
-- response, as a real confidence signal for table/figure captions: was
-- this caption linked to its table/figure by the parser's own explicit
-- Tier-1 heuristic (pdfplumber text-prefix + bbox proximity, FEAT-027),
-- by chunker.py's own Tier-2 proximity fallback, or left "unmatched"
-- (genuinely uncertain)? This information was already being computed and
-- stored (db/queries.py's build_chunk_rows already writes it into
-- chunks.metadata) but was never SELECTED anywhere between storage and
-- the client -- real, free information discarded at the retrieval
-- boundary for no reason. No new computation, just retrieval.
--
-- Same real constraint FEAT-020's document_mime_type addition hit
-- (migration 20260727_001_citation_document_mime_type.sql): Postgres
-- does not allow CREATE OR REPLACE FUNCTION to change a function's
-- RETURNS TABLE column list -- confirmed against that migration's own
-- note ("cannot change return type of existing function... Use DROP
-- FUNCTION first"). Same fix: DROP + CREATE, same signatures, so the
-- existing `client.rpc("match_chunks_by_vector"/"match_chunks_by_fts", ...)`
-- call sites in services/retriever.py need no changes beyond reading the
-- new column.

drop function if exists match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider);
drop function if exists match_chunks_by_fts(text, uuid, uuid[], int);

create function match_chunks_by_vector(
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

create function match_chunks_by_fts(
  query_text text,
  match_user_id uuid,
  match_document_ids uuid[],
  match_limit int
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
  rank float4
)
language sql stable
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
    ts_rank(c.ts, websearch_to_tsquery('english', query_text)) as rank
  from chunks c
  join documents d on d.id = c.document_id
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.ts @@ websearch_to_tsquery('english', query_text)
  order by rank desc
  limit match_limit;
$$;

revoke execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider) from public;
revoke execute on function match_chunks_by_fts(text, uuid, uuid[], int) from public;
grant execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider) to service_role;
grant execute on function match_chunks_by_fts(text, uuid, uuid[], int) to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop function if exists match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider);
-- drop function if exists match_chunks_by_fts(text, uuid, uuid[], int);
-- -- then re-run 20260731_001_embedding_provider_fallback.sql's
-- -- match_chunks_by_vector CREATE statement and 20260727_001's
-- -- match_chunks_by_fts CREATE statement to restore the pre-this-migration shape.
