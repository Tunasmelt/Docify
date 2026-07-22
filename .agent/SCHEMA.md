# Schema

Source of truth for the Postgres schema. Changes to this file require: (1) a migration file added, (2) a CHANGELOG entry with rollback SQL, (3) human confirmation before merge.

**Multi-tenancy is enforced at the DB layer via RLS.** Every user-owned table has `user_id uuid not null references auth.users(id) on delete cascade`, and every such table has policies that restrict rows to `auth.uid() = user_id`. Application code must not bypass this.

---

## Extensions

```sql
create extension if not exists vector;      -- pgvector for embeddings
create extension if not exists pgcrypto;    -- gen_random_uuid()
-- Postgres FTS is built in; no extension needed for tsvector/BM25-lite
```

---

## Enums

```sql
create type document_status as enum ('uploaded', 'parsing', 'embedded', 'ready', 'failed');
create type element_type    as enum ('text', 'heading', 'table', 'figure', 'caption', 'list');
create type message_role    as enum ('user', 'assistant');
create type verdict         as enum ('supported', 'partial', 'unsupported');
```

---

## Tables

### `documents`
One row per uploaded document.

```sql
create table documents (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  filename       text not null,
  storage_path   text not null,             -- uploads/{user_id}/{uuid}.pdf
  mime_type      text not null,
  size_bytes     bigint not null,
  page_count     int,                       -- null until parsed
  status         document_status not null default 'uploaded',
  error          text,                      -- populated on status='failed'
  metadata       jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  parsed_at      timestamptz,
  embedded_at    timestamptz
);

create index documents_user_idx    on documents(user_id, created_at desc);
create index documents_status_idx  on documents(status) where status in ('uploaded','parsing','embedded');
```

### `chunks`
One row per retrievable unit. Text and figure-caption chunks both go here; the embedding is Voyage's (1024 dims by default, matryoshka-truncated if needed later).

```sql
create table chunks (
  id             uuid primary key default gen_random_uuid(),
  document_id    uuid not null references documents(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  chunk_index    int not null,              -- ordinal position within document
  element_type   element_type not null,
  page_number    int not null,
  bbox           jsonb,                     -- {x0,y0,x1,y1} on the source page
  content        text not null,             -- the extracted text
  figure_path    text,                      -- storage path if element_type='figure'
  embedding      vector(1024) not null,
  ts             tsvector generated always as (to_tsvector('english', content)) stored,
  metadata       jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create index chunks_document_idx on chunks(document_id);
create index chunks_user_idx     on chunks(user_id);
create index chunks_ts_idx       on chunks using gin(ts);
create index chunks_embedding_idx on chunks
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
```

### `conversations`
Grouping of Q&A over one or more documents.

```sql
create table conversations (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  title         text,                       -- auto-generated from first question, editable
  document_ids  uuid[] not null,            -- documents in scope for this conversation
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index conversations_user_idx on conversations(user_id, updated_at desc);
```

### `messages`
Individual turns within a conversation.

```sql
create table messages (
  id               uuid primary key default gen_random_uuid(),
  conversation_id  uuid not null references conversations(id) on delete cascade,
  user_id          uuid not null references auth.users(id) on delete cascade,
  role             message_role not null,
  content          text not null,           -- final rendered content (post-verification)
  raw_content      text,                    -- pre-verification content, for audit
  retrieved_chunk_ids uuid[],               -- chunks fed to the model for this turn
  metadata         jsonb not null default '{}'::jsonb,  -- model, tokens, latency, etc.
  created_at       timestamptz not null default now()
);

create index messages_conv_idx on messages(conversation_id, created_at);
create index messages_user_idx on messages(user_id, created_at desc);
```

### `citations`
One row per (message, cited chunk) pair, with the verifier verdict.

```sql
create table citations (
  id               uuid primary key default gen_random_uuid(),
  message_id       uuid not null references messages(id) on delete cascade,
  chunk_id         uuid not null references chunks(id) on delete cascade,
  user_id          uuid not null references auth.users(id) on delete cascade,
  claim_span       text not null,           -- the specific claim being verified
  claim_start      int,                     -- char offset in message.content
  claim_end        int,
  verdict          verdict not null,
  supporting_quote text,                    -- verifier's quoted span from source
  verifier_model   text not null,           -- e.g. 'claude-haiku-4-5-20251001'
  verified_at      timestamptz not null default now()
);

create index citations_message_idx on citations(message_id);
create index citations_chunk_idx   on citations(chunk_id);
create index citations_user_idx    on citations(user_id);
```

---

## Row-Level Security policies

RLS is enabled on every user-owned table. The policy is uniform: `auth.uid() = user_id`. This is the load-bearing multi-tenancy mechanism.

```sql
-- documents
alter table documents enable row level security;
create policy documents_select on documents for select using (auth.uid() = user_id);
create policy documents_insert on documents for insert with check (auth.uid() = user_id);
create policy documents_update on documents for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy documents_delete on documents for delete using (auth.uid() = user_id);

-- chunks (writes come from service-role in FastAPI, still user_id-scoped for reads)
alter table chunks enable row level security;
create policy chunks_select on chunks for select using (auth.uid() = user_id);
-- No user-facing insert/update/delete policy — service-role bypasses RLS by design

-- conversations
alter table conversations enable row level security;
create policy conversations_select on conversations for select using (auth.uid() = user_id);
create policy conversations_insert on conversations for insert with check (auth.uid() = user_id);
create policy conversations_update on conversations for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy conversations_delete on conversations for delete using (auth.uid() = user_id);

-- messages
alter table messages enable row level security;
create policy messages_select on messages for select using (auth.uid() = user_id);
-- Inserts come from service-role during /query

-- citations
alter table citations enable row level security;
create policy citations_select on citations for select using (auth.uid() = user_id);
-- Inserts come from service-role during /query
```

**Service-role client discipline** (from FastAPI):
- Service-role bypasses RLS. This is required because FastAPI writes rows on behalf of authenticated users.
- Every INSERT and SELECT still includes `user_id` explicitly in the payload / WHERE clause.
- Never expose the service-role key to the frontend or in any client-side code.

---

## Supabase Storage buckets & policies

Two buckets, both private, both RLS-scoped to `user_id` in the path.

```
uploads/{user_id}/{document_uuid}.pdf       -- original uploaded PDFs
figures/{user_id}/{document_id}/{fig}.png   -- cropped figure images from parsing
```

Storage policies (Supabase Dashboard or SQL):

```sql
-- uploads bucket
create policy uploads_select on storage.objects for select
  using (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);
create policy uploads_insert on storage.objects for insert
  with check (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);
create policy uploads_delete on storage.objects for delete
  using (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);

-- figures bucket — read-only from user; writes are service-role only
create policy figures_select on storage.objects for select
  using (bucket_id = 'figures' and (storage.foldername(name))[1] = auth.uid()::text);
```

---

## Indexing notes

- **HNSW on embeddings** is the default choice — faster query time than IVFFlat once data is loaded, marginally slower to build. At portfolio scale this doesn't matter; correctness is what matters.
- **GIN on tsvector** enables Postgres FTS for the BM25-lite half of hybrid search. The `ts` column is a generated stored column so it stays in sync automatically.
- **Composite index on `(user_id, created_at desc)`** for list views is more important than it looks — it's the difference between fast dashboard loads and full scans.

---

## Migration convention

- Migrations live in `apps/api/migrations/` as timestamped SQL files: `20260721_001_initial.sql`
- Each migration is idempotent where possible (`create ... if not exists`, `drop policy if exists`)
- Migrations are applied via Supabase CLI (`supabase db push`) in dev; in prod, run them manually against the prod project before deploy
- Every migration file has a matching `-- ROLLBACK` block at the bottom documenting reversal
- Schema changes require a CHANGELOG entry

---

## What's not in the schema (deliberately)

- No `users` table of our own — Supabase Auth owns it (`auth.users`)
- No `sessions` table — Supabase Auth handles session state via JWT
- No `subscriptions` / `billing` tables — not in scope
- No `organizations` / `teams` — Phase 4+ if ever
- No soft-delete columns (yet) — hard delete via cascade for now; add if data retention becomes a concern

---

## Migration log

| Date | Migration | Summary |
|---|---|---|
| — | — | No migrations applied yet. First migration will be `20260721_001_initial.sql` implementing everything above. |
