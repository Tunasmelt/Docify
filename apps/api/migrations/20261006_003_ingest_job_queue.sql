-- 2026-10-06: Postgres-backed ingest job queue + atomic chunk swap.
--
-- Ingest used to run as a FastAPI BackgroundTask in the request-serving
-- process: unbounded concurrency across users, lost on restart (the
-- stuck-document reaper marked it failed 30 minutes later), no automatic
-- retry, no time limit. Now /ingest and /reindex enqueue a job here and a
-- worker thread claims jobs one at a time (services/ingest_queue.py).
--
-- Reindex used to delete a document's chunks before reprocessing, so the
-- document answered nothing while it ran and stayed empty if it failed.
-- New chunks now go to chunks_staging and swap_document_chunks() replaces
-- the live set in one transaction.

-- ── Job queue ────────────────────────────────────────────────────────────

create table if not exists ingest_jobs (
  id            uuid primary key default gen_random_uuid(),
  document_id   uuid not null references documents(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  storage_path  text not null,
  status        text not null default 'queued'
                check (status in ('queued', 'running', 'succeeded', 'failed')),
  attempts      int not null default 0,
  max_attempts  int not null default 3,
  -- Earliest time a queued job may be claimed (retry backoff).
  run_after     timestamptz not null default now(),
  -- Heartbeat: a running job whose locked_at is older than the lease is
  -- presumed abandoned (worker crashed or the instance restarted) and is
  -- claimed again.
  locked_at     timestamptz,
  locked_by     text,
  last_error    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- At most one active job per document.
create unique index if not exists ingest_jobs_one_active_per_document
  on ingest_jobs (document_id) where status in ('queued', 'running');
create index if not exists ingest_jobs_queued_idx
  on ingest_jobs (created_at) where status = 'queued';

-- Only the API (service role) touches jobs: RLS on with no policies.
alter table ingest_jobs enable row level security;
grant select, insert, update, delete on ingest_jobs to service_role;

-- Claims the oldest runnable job, or nothing. Runnable: queued and due, or
-- running with an expired lease. Returns nothing while p_max_running jobs
-- already hold live leases, which is the global concurrency cap across all
-- users and all API instances. The advisory lock serializes claimers so
-- that count can't race.
create or replace function claim_ingest_job(p_worker_id text, p_lease_seconds int, p_max_running int)
returns setof ingest_jobs
language plpgsql
as $$
declare
  v_lease interval := make_interval(secs => p_lease_seconds);
  v_running int;
  v_job ingest_jobs;
begin
  perform pg_advisory_xact_lock(hashtext('claim_ingest_job'));

  select count(*) into v_running
  from ingest_jobs
  where status = 'running' and locked_at > now() - v_lease;
  if v_running >= p_max_running then
    return;
  end if;

  select * into v_job
  from ingest_jobs
  where (status = 'queued' and run_after <= now())
     or (status = 'running' and locked_at <= now() - v_lease)
  order by created_at
  limit 1
  for update skip locked;
  if not found then
    return;
  end if;

  update ingest_jobs
  set status = 'running', attempts = attempts + 1, locked_at = now(), locked_by = p_worker_id, updated_at = now()
  where id = v_job.id
  returning * into v_job;
  return next v_job;
end;
$$;

revoke execute on function claim_ingest_job(text, int, int) from public, anon, authenticated;
grant execute on function claim_ingest_job(text, int, int) to service_role;

-- ── Atomic chunk swap ────────────────────────────────────────────────────

-- Same columns as chunks minus the generated ts column, with no unique
-- constraint or vector index: rows only live here until the swap.
create table if not exists chunks_staging (
  id                 uuid primary key default gen_random_uuid(),
  document_id        uuid not null references documents(id) on delete cascade,
  user_id            uuid not null references auth.users(id) on delete cascade,
  chunk_index        int not null,
  element_type       element_type not null,
  page_number        int not null,
  bbox               jsonb,
  content            text not null,
  figure_path        text,
  embedding          vector(1024) not null,
  embedding_provider embedding_provider not null default 'voyage',
  metadata           jsonb not null default '{}'::jsonb,
  created_at         timestamptz not null default now()
);
create index if not exists chunks_staging_document_idx on chunks_staging (document_id);
alter table chunks_staging enable row level security;
grant select, insert, update, delete on chunks_staging to service_role;

-- Replaces a document's live chunks with its staged ones in one
-- transaction: searches see either the old set or the new one, never a
-- mix or nothing. Returns the number of chunks now live. Note that
-- citations reference chunks with ON DELETE CASCADE, so (as before this
-- migration) a reindex still removes citations to the old chunks.
create or replace function swap_document_chunks(p_document_id uuid, p_user_id uuid)
returns int
language plpgsql
as $$
declare
  v_count int;
begin
  delete from chunks where document_id = p_document_id and user_id = p_user_id;
  insert into chunks (
    id, document_id, user_id, chunk_index, element_type, page_number, bbox, content,
    figure_path, embedding, embedding_provider, metadata, created_at
  )
  select
    id, document_id, user_id, chunk_index, element_type, page_number, bbox, content,
    figure_path, embedding, embedding_provider, metadata, created_at
  from chunks_staging
  where document_id = p_document_id and user_id = p_user_id;
  get diagnostics v_count = row_count;
  delete from chunks_staging where document_id = p_document_id and user_id = p_user_id;
  return v_count;
end;
$$;

revoke execute on function swap_document_chunks(uuid, uuid) from public, anon, authenticated;
grant execute on function swap_document_chunks(uuid, uuid) to service_role;

-- ROLLBACK
-- drop function if exists swap_document_chunks(uuid, uuid);
-- drop table if exists chunks_staging;
-- drop function if exists claim_ingest_job(text, int, int);
-- drop table if exists ingest_jobs;
