-- 20261006_001_fts_any_term_matching.sql
--
-- match_chunks_by_fts used websearch_to_tsquery('english', question), which
-- ANDs every non-stopword term: "What was the revenue growth in Q3?" became
-- 'revenu' & 'growth' & 'q3', so a chunk had to contain all of them. Natural-
-- language questions almost never matched, which silently reduced hybrid
-- retrieval to vector-only.
--
-- Now any term matches (OR), and ts_rank orders chunks that match more terms
-- higher. The terms are the question's own english-normalized lexemes
-- (to_tsvector: stopwords dropped, words stemmed), cast straight to tsquery so
-- they are not re-normalized. A question with no usable terms (all stopwords)
-- yields NULL and matches nothing, as before.
--
-- Same signature and RETURNS TABLE as 20260802_002, so CREATE OR REPLACE is
-- enough and existing grants carry over.

create or replace function fts_any_term_query(query_text text)
returns tsquery
language sql
immutable
as $$
  select string_agg(
           '''' || replace(replace(lexeme, '\', '\\'), '''', '''''') || '''',
           ' | '
         )::tsquery
  from unnest(to_tsvector('english', query_text));
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
  with q as (select fts_any_term_query(query_text) as tsq)
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
    ts_rank(c.ts, q.tsq) as rank
  from chunks c
  join documents d on d.id = c.document_id
  cross join q
  where c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.ts @@ q.tsq
  order by rank desc
  limit match_limit;
$$;

revoke execute on function fts_any_term_query(text) from public;
grant execute on function fts_any_term_query(text) to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- Re-run the match_chunks_by_fts CREATE statement from
-- 20260802_002_citation_association_method.sql (as CREATE OR REPLACE), then:
-- drop function if exists fts_any_term_query(text);
