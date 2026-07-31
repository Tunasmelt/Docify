-- 20260731_001_embedding_provider_fallback.sql
--
-- Voyage embedding fallback via Gemini (services/embedder.py). Real
-- failure mode this closes: a mid-batch Voyage RPM exhaustion (this
-- account's real, empirically-observed 3 RPM free-tier ceiling —
-- .agent/MEMORY.md), not a clean whole-document failure — so provider
-- tracking is per CHUNK, not per document. A single document's chunks
-- can legitimately end up split across both providers if only some
-- batches hit the fallback.
--
-- CRITICAL CONSTRAINT, not an implementation detail: a Voyage-embedded
-- vector and a Gemini-embedded vector are NOT comparable via cosine
-- similarity even at matching dimensionality (1024) — they are points in
-- two different, unrelated embedding spaces that merely happen to share
-- a dimension count. match_chunks_by_vector below is rewritten to take
-- an explicit match_provider parameter and filter on it — retriever.py
-- calls it once per distinct provider actually present in a given
-- document_ids scope, NEVER compares across the filter. See
-- .agent/MEMORY.md's standing anti-pattern entry before changing this.

do $$ begin
  create type embedding_provider as enum ('voyage', 'gemini');
exception when duplicate_object then null; end $$;

alter table chunks
  add column if not exists embedding_provider embedding_provider not null default 'voyage';

-- match_chunks_by_vector's signature changes (new trailing parameter) —
-- CREATE OR REPLACE does NOT replace a function when the parameter list
-- changes; it would silently create a second overload alongside the old
-- 4-arg one. The old signature is dropped explicitly first.
drop function if exists match_chunks_by_vector(vector(1024), uuid, uuid[], int);

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
    c.embedding <=> query_embedding as distance
  from chunks c
  join documents d on d.id = c.document_id
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.embedding_provider = match_provider
  order by c.embedding <=> query_embedding
  limit match_limit;
$$;

revoke execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider) from public;
grant execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider) to service_role;

-- distinct_embedding_providers: retriever.py's cost guard — "skip
-- Gemini's query-embed call entirely if no Gemini-tagged chunks are in
-- scope" (task brief item 4) needs to know which providers are actually
-- present for a given document_ids scope BEFORE deciding which
-- embed_query() calls to make. A tiny, index-friendly query (at most 2
-- distinct values ever), not a full row scan client-side.
create function distinct_embedding_providers(
  match_user_id uuid,
  match_document_ids uuid[]
)
returns table (embedding_provider embedding_provider)
language sql stable
as $$
  select distinct c.embedding_provider
  from chunks c
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids);
$$;

revoke execute on function distinct_embedding_providers(uuid, uuid[]) from public;
grant execute on function distinct_embedding_providers(uuid, uuid[]) to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop function if exists distinct_embedding_providers(uuid, uuid[]);
-- drop function if exists match_chunks_by_vector(vector(1024), uuid, uuid[], int, embedding_provider);
-- create function match_chunks_by_vector(
--   query_embedding vector(1024),
--   match_user_id uuid,
--   match_document_ids uuid[],
--   match_limit int
-- )
-- returns table (
--   id uuid, document_id uuid, document_name text, document_mime_type text, chunk_index int,
--   element_type element_type, page_number int, content text, distance float8
-- )
-- language sql stable
-- as $$
--   select c.id, c.document_id, d.filename as document_name, d.mime_type as document_mime_type,
--     c.chunk_index, c.element_type, c.page_number, c.content, c.embedding <=> query_embedding as distance
--   from chunks c join documents d on d.id = c.document_id
--   where c.user_id = match_user_id and c.document_id = any(match_document_ids)
--   order by c.embedding <=> query_embedding limit match_limit;
-- $$;
-- grant execute on function match_chunks_by_vector(vector(1024), uuid, uuid[], int) to service_role;
-- alter table chunks drop column if exists embedding_provider;
-- drop type if exists embedding_provider;
