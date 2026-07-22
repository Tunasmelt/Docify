-- migration1script.sql (originally authored as 20260722_001_initial.sql)
-- Initial schema per .agent/SCHEMA.md. Multi-tenant via RLS from day one.

-- ── Extensions ──────────────────────────────────────────────────────────────
create extension if not exists vector;
create extension if not exists pgcrypto;

-- ── Enums ───────────────────────────────────────────────────────────────────
do $$ begin
  create type document_status as enum ('uploaded', 'parsing', 'embedded', 'ready', 'failed');
exception when duplicate_object then null; end $$;

do $$ begin
  create type element_type as enum ('text', 'heading', 'table', 'figure', 'caption', 'list');
exception when duplicate_object then null; end $$;

do $$ begin
  create type message_role as enum ('user', 'assistant');
exception when duplicate_object then null; end $$;

do $$ begin
  create type verdict as enum ('supported', 'partial', 'unsupported');
exception when duplicate_object then null; end $$;

-- ── Tables ──────────────────────────────────────────────────────────────────

create table if not exists documents (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  filename       text not null,
  storage_path   text not null,
  mime_type      text not null,
  size_bytes     bigint not null,
  page_count     int,
  status         document_status not null default 'uploaded',
  error          text,
  metadata       jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  parsed_at      timestamptz,
  embedded_at    timestamptz
);

create index if not exists documents_user_idx   on documents(user_id, created_at desc);
create index if not exists documents_status_idx on documents(status) where status in ('uploaded','parsing','embedded');

create table if not exists chunks (
  id             uuid primary key default gen_random_uuid(),
  document_id    uuid not null references documents(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  chunk_index    int not null,
  element_type   element_type not null,
  page_number    int not null,
  bbox           jsonb,
  content        text not null,
  figure_path    text,
  embedding      vector(1024) not null,
  ts             tsvector generated always as (to_tsvector('english', content)) stored,
  metadata       jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create index if not exists chunks_document_idx  on chunks(document_id);
create index if not exists chunks_user_idx      on chunks(user_id);
create index if not exists chunks_ts_idx        on chunks using gin(ts);
create index if not exists chunks_embedding_idx on chunks
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

create table if not exists conversations (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  title         text,
  document_ids  uuid[] not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists conversations_user_idx on conversations(user_id, updated_at desc);

create table if not exists messages (
  id                   uuid primary key default gen_random_uuid(),
  conversation_id      uuid not null references conversations(id) on delete cascade,
  user_id              uuid not null references auth.users(id) on delete cascade,
  role                 message_role not null,
  content              text not null,
  raw_content          text,
  retrieved_chunk_ids  uuid[],
  metadata             jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now()
);

create index if not exists messages_conv_idx on messages(conversation_id, created_at);
create index if not exists messages_user_idx on messages(user_id, created_at desc);

create table if not exists citations (
  id               uuid primary key default gen_random_uuid(),
  message_id       uuid not null references messages(id) on delete cascade,
  chunk_id         uuid not null references chunks(id) on delete cascade,
  user_id          uuid not null references auth.users(id) on delete cascade,
  claim_span       text not null,
  claim_start      int,
  claim_end        int,
  verdict          verdict not null,
  supporting_quote text,
  verifier_model   text not null,
  verified_at      timestamptz not null default now()
);

create index if not exists citations_message_idx on citations(message_id);
create index if not exists citations_chunk_idx   on citations(chunk_id);
create index if not exists citations_user_idx    on citations(user_id);

-- ── Row-Level Security ──────────────────────────────────────────────────────

alter table documents enable row level security;
drop policy if exists documents_select on documents;
create policy documents_select on documents for select using (auth.uid() = user_id);
drop policy if exists documents_insert on documents;
create policy documents_insert on documents for insert with check (auth.uid() = user_id);
drop policy if exists documents_update on documents;
create policy documents_update on documents for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists documents_delete on documents;
create policy documents_delete on documents for delete using (auth.uid() = user_id);

alter table chunks enable row level security;
drop policy if exists chunks_select on chunks;
create policy chunks_select on chunks for select using (auth.uid() = user_id);
-- No user-facing insert/update/delete policy — service-role bypasses RLS by design

alter table conversations enable row level security;
drop policy if exists conversations_select on conversations;
create policy conversations_select on conversations for select using (auth.uid() = user_id);
drop policy if exists conversations_insert on conversations;
create policy conversations_insert on conversations for insert with check (auth.uid() = user_id);
drop policy if exists conversations_update on conversations;
create policy conversations_update on conversations for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists conversations_delete on conversations;
create policy conversations_delete on conversations for delete using (auth.uid() = user_id);

alter table messages enable row level security;
drop policy if exists messages_select on messages;
create policy messages_select on messages for select using (auth.uid() = user_id);
-- Inserts come from service-role during /query

alter table citations enable row level security;
drop policy if exists citations_select on citations;
create policy citations_select on citations for select using (auth.uid() = user_id);
-- Inserts come from service-role during /query

-- ── Storage buckets ─────────────────────────────────────────────────────────

insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('figures', 'figures', false)
on conflict (id) do nothing;

drop policy if exists uploads_select on storage.objects;
create policy uploads_select on storage.objects for select
  using (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists uploads_insert on storage.objects;
create policy uploads_insert on storage.objects for insert
  with check (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists uploads_delete on storage.objects;
create policy uploads_delete on storage.objects for delete
  using (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists figures_select on storage.objects;
create policy figures_select on storage.objects for select
  using (bucket_id = 'figures' and (storage.foldername(name))[1] = auth.uid()::text);

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop policy if exists figures_select on storage.objects;
-- drop policy if exists uploads_delete on storage.objects;
-- drop policy if exists uploads_insert on storage.objects;
-- drop policy if exists uploads_select on storage.objects;
-- delete from storage.buckets where id in ('uploads', 'figures');
--
-- drop table if exists citations;
-- drop table if exists messages;
-- drop table if exists conversations;
-- drop table if exists chunks;
-- drop table if exists documents;
--
-- drop type if exists verdict;
-- drop type if exists message_role;
-- drop type if exists element_type;
-- drop type if exists document_status;
