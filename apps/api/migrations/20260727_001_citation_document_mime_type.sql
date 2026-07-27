-- 20260727_001_citation_document_mime_type.sql
--
-- FEAT-020 (DOCX/PPTX/HTML ingestion) found a real, user-facing gap while
-- fixing source-panel.tsx's hardcoded "PAGE N" citation display: the
-- citation-display layer has NO way to know which format a citation's
-- source document actually is. `documents.mime_type` has existed since
-- FEAT-001, but nothing between it and the client ever selects it —
-- match_chunks_by_vector/match_chunks_by_fts (FEAT-009) join `documents`
-- for `filename` only, and CITATION_JOIN_COLUMNS (FEAT-026) does the same.
--
-- Without this, a DOCX/HTML citation (page_number is an honest but
-- meaningless `1` sentinel — Docling gives no real page concept for these
-- formats, FEAT-020's own SCHEMA.md note) would keep displaying "PAGE 1"
-- as if it were a real location, and a PPTX citation (page_number is
-- really the slide index) would display "PAGE N" where "SLIDE N" is what
-- it actually means.
--
-- match_chunks_by_vector/match_chunks_by_fts's RETURNS TABLE column list
-- is changing (a new column), which Postgres does not allow via
-- CREATE OR REPLACE FUNCTION alone — confirmed live against the local
-- stack: "cannot change return type of existing function... Use DROP
-- FUNCTION first." DROP + CREATE in the same migration, same signature,
-- so the existing `client.rpc("match_chunks_by_vector", ...)` call sites
-- in services/retriever.py need no changes beyond reading the new column.

drop function if exists match_chunks_by_vector(vector(1024), uuid, uuid[], int);
drop function if exists match_chunks_by_fts(text, uuid, uuid[], int);

create function match_chunks_by_vector(
  query_embedding vector(1024),
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
-- -- then re-run 20260724_001_hybrid_search_functions.sql's CREATE statements
-- -- to restore the pre-FEAT-020 5-column shape.
