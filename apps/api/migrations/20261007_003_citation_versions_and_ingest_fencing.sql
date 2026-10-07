select '[1]'::vector;
-- Retain cited evidence versions; only current chunks participate in retrieval.
alter table chunks add column if not exists archived boolean not null default false;
alter table chunks drop constraint if exists chunks_document_id_chunk_index_key;
create unique index if not exists chunks_current_document_index
  on chunks(document_id, chunk_index) where not archived;

create or replace function swap_document_chunks(p_document_id uuid, p_user_id uuid)
returns int language plpgsql as $$
declare v_count int;
begin
  -- Serialize replacement against another replacement of this document.
  perform 1 from documents where id=p_document_id and user_id=p_user_id for update;
  if not found then raise exception 'document not found'; end if;
  update chunks c set archived=true
    where document_id=p_document_id and user_id=p_user_id and not archived
      and exists (select 1 from citations s where s.chunk_id=c.id);
  -- Also reclaim historical versions whose citations were subsequently deleted.
  delete from chunks c where document_id=p_document_id and user_id=p_user_id
    and not exists(select 1 from citations s where s.chunk_id=c.id);
  insert into chunks (id,document_id,user_id,chunk_index,element_type,page_number,bbox,content,
    figure_path,embedding,embedding_provider,metadata,created_at)
  select id,document_id,user_id,chunk_index,element_type,page_number,bbox,content,
    figure_path,embedding,embedding_provider,metadata,created_at
  from chunks_staging where document_id=p_document_id and user_id=p_user_id;
  get diagnostics v_count=row_count;
  delete from chunks_staging where document_id=p_document_id and user_id=p_user_id;
  return v_count;
end;
$$;

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
  where not c.archived
    and c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.embedding_provider = match_provider
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
  where not c.archived
    and c.user_id = match_user_id
    and c.document_id = any(match_document_ids)
    and c.ts @@ q.tsq
  order by rank desc
  limit match_limit;
$$;


create or replace function distinct_embedding_providers(match_user_id uuid, match_document_ids uuid[])
returns table (embedding_provider embedding_provider) language sql stable as $$
  select distinct c.embedding_provider from chunks c
  where not c.archived and c.user_id=match_user_id and c.document_id=any(match_document_ids);
$$;

-- All ingest mutations lock and validate the claim in the same transaction.
-- A stale worker cannot alter progress, staging, live chunks or job state.
create or replace function mutate_ingest_attempt(
  p_job_id uuid, p_user_id uuid, p_worker_id text, p_attempt int,
  p_action text, p_values jsonb default '{}'::jsonb, p_rows jsonb default '[]'::jsonb
) returns boolean language plpgsql as $$
declare j ingest_jobs;
begin
  select * into j from ingest_jobs
    where id=p_job_id and user_id=p_user_id for update;
  if not found or j.status <> 'running' or j.locked_by is distinct from p_worker_id
    or j.attempts <> p_attempt or j.locked_at is null or j.locked_at <= now()-interval '120 seconds' then
    return false;
  end if;
  if p_action='progress' then
    update documents set
      status=coalesce((p_values->>'status')::document_status,status),
      error=case when p_values ? 'error' then p_values->>'error' else error end,
      page_count=case when p_values ? 'page_count' then (p_values->>'page_count')::int else page_count end,
      parsed_at=coalesce((p_values->>'parsed_at')::timestamptz,parsed_at),
      embedded_at=coalesce((p_values->>'embedded_at')::timestamptz,embedded_at),
      updated_at=now()
    where id=j.document_id and user_id=p_user_id;
  elsif p_action='discard' then
    delete from chunks_staging where document_id=j.document_id and user_id=p_user_id;
  elsif p_action='stage' then
    if exists(select 1 from jsonb_array_elements(p_rows) r
      where (r->>'document_id')::uuid is distinct from j.document_id
        or (r->>'user_id')::uuid is distinct from p_user_id) then
      raise exception 'staged chunk owner mismatch';
    end if;
    insert into chunks_staging (id,document_id,user_id,chunk_index,element_type,page_number,bbox,
      content,figure_path,embedding,embedding_provider,metadata)
    select coalesce(id,gen_random_uuid()),document_id,user_id,chunk_index,element_type,page_number,bbox,
      content,figure_path,embedding,embedding_provider,coalesce(metadata,'{}'::jsonb)
    from jsonb_populate_recordset(null::chunks_staging,p_rows);
  elsif p_action='publish' then
    perform swap_document_chunks(j.document_id,p_user_id);
    update documents set status='ready',error=null where id=j.document_id and user_id=p_user_id;
  elsif p_action='retry' then
    update ingest_jobs set status='queued',run_after=(p_values->>'run_after')::timestamptz,
      last_error=p_values->>'error',locked_at=null,locked_by=null,updated_at=now() where id=j.id;
    update documents set status='uploaded',error=null,updated_at=now() where id=j.document_id and user_id=p_user_id;
  elsif p_action='finish' then
    update ingest_jobs set status=p_values->>'status',last_error=p_values->>'error',
      locked_at=null,locked_by=null,updated_at=now() where id=j.id;
  else raise exception 'unknown ingest action';
  end if;
  return true;
end;
$$;
revoke execute on function mutate_ingest_attempt(uuid,uuid,text,int,text,jsonb,jsonb) from public,anon,authenticated;
grant execute on function mutate_ingest_attempt(uuid,uuid,text,int,text,jsonb,jsonb) to service_role;
notify pgrst, 'reload schema';
