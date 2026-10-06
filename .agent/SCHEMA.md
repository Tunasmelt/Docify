# Schema

Source of truth for the Postgres schema. Changes to this file require: (1) a migration file added, (2) a CHANGELOG entry with rollback SQL, (3) human confirmation before merge.

**Multi-tenancy.** Every user-owned table has `user_id uuid not null references auth.users(id) on delete cascade` and RLS policies restricting rows to `auth.uid() = user_id`. RLS protects every request made with the anon key + a user JWT (the browser's direct Supabase calls). **FastAPI uses the service-role key, which bypasses RLS** — on that path the explicit `user_id` filter in every query and RPC is the only tenant boundary. See ARCHITECTURE.md §Multi-tenancy and §Service-role client discipline below.

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
create type document_status     as enum ('uploaded', 'parsing', 'embedded', 'ready', 'failed');
create type element_type       as enum ('text', 'heading', 'table', 'figure', 'caption', 'list');
create type message_role       as enum ('user', 'assistant');
create type verdict            as enum ('supported', 'partial', 'unsupported', 'unverified');
-- 'unverified' = verification could not run (Gemini error/timeout/malformed response);
-- never a substitute for a real 'unsupported' verdict. See services/verifier.py.
create type embedding_provider as enum ('voyage', 'gemini');  -- see chunks.embedding_provider below
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
  storage_path   text not null,             -- uploads/{user_id}/{uuid}.{ext}
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
One row per retrievable unit. Text and figure-caption chunks both go here; the embedding is Voyage's by default (1024 dims), or Gemini's (`gemini-embedding-2`, truncated to the same 1024 dims) for a chunk that hit the fallback — see `embedding_provider` below.

```sql
create table chunks (
  id                 uuid primary key default gen_random_uuid(),
  document_id        uuid not null references documents(id) on delete cascade,
  user_id            uuid not null references auth.users(id) on delete cascade,
  chunk_index        int not null,              -- ordinal position within document
  element_type       element_type not null,
  page_number        int not null,
  bbox               jsonb,                     -- {x0,y0,x1,y1} on the source page
  content            text not null,             -- the extracted text
  figure_path        text,                      -- storage path if element_type='figure'
  embedding          vector(1024) not null,
  embedding_provider embedding_provider not null default 'voyage',  -- see note below
  ts                 tsvector generated always as (to_tsvector('english', content)) stored,
  metadata           jsonb not null default '{}'::jsonb,
  created_at         timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create index chunks_document_idx on chunks(document_id);
create index chunks_user_idx     on chunks(user_id);
create index chunks_ts_idx       on chunks using gin(ts);
create index chunks_embedding_idx on chunks
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
```

**`embedding_provider` — critical constraint.** `services/embedder.py` falls back to Gemini (`gemini-embedding-2`, truncated to 1024 dims) for a batch only once Voyage's own retries are exhausted (typically its 3 RPM free-tier ceiling mid-ingest). Tracking is per chunk because one document's chunks can be split across both providers.

**A Voyage vector and a Gemini vector are not comparable by cosine similarity**, even though both are `vector(1024)` — they live in unrelated embedding spaces. `match_chunks_by_vector` takes a `match_provider` parameter and filters on it; `Retriever.retrieve()` runs one vector search per provider present (via `distinct_embedding_providers`) and fuses them with FTS by rank only. Read `.agent/MEMORY.md §Anti-patterns` (2026-07-31) before touching this.

**`page_number` and `bbox` depend on the source format** (set by `services/parser.py`):
- **PDF:** the real 1-indexed page number and real bbox.
- **PPTX:** the real 1-indexed slide number (the column name stays `page_number` for API stability). Bbox comes from the shape's position.
- **DOCX / HTML:** these formats have no pagination, so every chunk gets `page_number = 1` and a zero-size sentinel bbox `{x0:0,y0:0,x1:0,y1:0}` meaning "no location available".

The frontend turns this into a display label in one place — `apps/web/lib/chat/parse-message.ts`'s `citationLocation()` (PDF → "page", PPTX → "slide", DOCX/HTML → omitted) — using `document_mime_type`, which the retrieval functions return alongside each chunk.

**`metadata->>'association_method'`** (`"explicit"` / `"heuristic"` / `"unmatched"` / null) records how a table/figure caption was linked to its element by the chunker. The retrieval functions return it and the API exposes it as `CitationResponse.association_method`; the frontend does not display it yet.

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
  marker           int not null,            -- the inline [N] marker in the answer (20260725_001)
  verdict          verdict not null,
  supporting_quote text,                    -- verifier's quoted span from source
  verifier_model   text not null,           -- e.g. 'gemini-3.5-flash-lite'
  verified_at      timestamptz not null default now()
);

create index citations_message_idx on citations(message_id);
create index citations_chunk_idx   on citations(chunk_id);
create index citations_user_idx    on citations(user_id);
```

### `usage_counters`
Postgres-backed daily rate-limit counters (`rate_limit.py`). Daily limits live here rather than in slowapi's memory so they survive Render's idle spin-down. One row per `(user_id, route, day)`; `route` is a label (`"ingest"` covers `/ingest` + `/reindex`, `"query"` covers `/query` + `/query/stream`). RLS is enabled with **no** policies, so only the service role can read or write it.

```sql
create table usage_counters (
  user_id  uuid not null references auth.users(id) on delete cascade,
  route    text not null,
  day      date not null,
  count    int not null default 0,
  primary key (user_id, route, day)
);
```
The composite primary key covers every lookup; incremented atomically by `increment_usage_counter()`.

---

## Row-Level Security policies

RLS is enabled on every user-owned table. The policy is uniform: `auth.uid() = user_id`. It is the tenant boundary for every request made with the anon key and a user JWT (the browser's direct Supabase access).

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

**Service-role client discipline** (FastAPI):
- The service role bypasses RLS. FastAPI needs it to write rows (chunks, messages, citations) that users cannot write directly.
- Because RLS does not apply, **the explicit `user_id` filter is the only tenant boundary on this path.** Every INSERT includes the JWT-derived `user_id`; every SELECT/UPDATE/DELETE and every RPC (`match_chunks_by_vector`, `match_chunks_by_fts`, `distinct_embedding_providers`, `create_query_turn`, `increment_usage_counter`) is scoped by it. A query missing it is a cross-tenant leak, not a style issue.
- Ownership lookups return the same 404 whether a row doesn't exist or belongs to another user.
- Never expose the service-role key to the frontend or any client-side code.

## Database functions (RPC)

PostgREST cannot express vector/FTS ranking or multi-table transactions, so these live in SQL and are called via `client.rpc(...)`:

| Function | Purpose | Defined / last changed |
|---|---|---|
| `match_chunks_by_vector(query_embedding, match_user_id, match_document_ids, match_limit, match_provider)` | Cosine search within one embedding provider; returns chunk + filename, mime type, association_method | `20260724_001`, `20260727_001`, `20260731_001`, `20260802_002` |
| `match_chunks_by_fts(query_text, match_user_id, match_document_ids, match_limit)` | Postgres FTS search; **any** question term matches, chunks matching more terms rank higher | `20260724_001`, `20260727_001`, `20260802_002`, `20261006_001` |
| `fts_any_term_query(query_text)` | Builds an OR `tsquery` from the question's english-normalized lexemes; NULL (matches nothing) if the question is all stopwords | `20261006_001` |
| `distinct_embedding_providers(match_user_id, match_document_ids)` | Which providers have chunks in a document scope | `20260731_001` |
| `create_query_turn(p_user_id, ...)` | Atomically writes conversation (if new) + 2 messages + citations; a malformed citation is skipped with a warning rather than rolling back the turn | `20260724_002`, `20260725_002`, `20260731_002` |
| `increment_usage_counter(p_user_id, p_route, p_day)` | Atomic upsert-and-increment for daily rate limits; executable by `service_role` only | `20260802_001` |

Changing a function's `RETURNS TABLE` columns requires `DROP FUNCTION` + `CREATE FUNCTION` — Postgres does not allow `CREATE OR REPLACE` to change them.

---

## Supabase Storage buckets & policies

Three buckets, all path-scoped `{user_id}/...`. `uploads` and `figures` are private, with RLS on SELECT too. `avatars` is deliberately public-read (an avatar is low-sensitivity, and public URLs avoid signed-URL refresh wherever identity renders) — see `migrations/20260804_001_avatars_bucket.sql`.

```
uploads/{user_id}/{uuid}.{ext}              -- original uploaded files (pdf/docx/pptx/html)
figures/{user_id}/{document_id}/{fig}.png   -- cropped figure images from parsing
avatars/{user_id}/avatar                    -- profile picture, one fixed object per user
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

-- avatars bucket — writes scoped to owner; SELECT open to any avatars row (content is
-- already public; upload(..., upsert: true) needs SELECT to resolve conflicts)
create policy avatars_insert on storage.objects for insert
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);
create policy avatars_update on storage.objects for update
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);
create policy avatars_delete on storage.objects for delete
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);
create policy avatars_select on storage.objects for select
  using (bucket_id = 'avatars');
```

**Storage objects do not cascade on `auth.users` deletion** — `storage.objects` has no foreign key to it. `DELETE /documents/{id}` removes that document's `uploads`/`figures` objects, and `DELETE /account` removes everything under `{user_id}/` in all three buckets, before any DB row is deleted.

---

## Indexing notes

- **HNSW on embeddings** is the default choice — faster query time than IVFFlat once data is loaded, marginally slower to build. At portfolio scale this doesn't matter; correctness is what matters.
- **GIN on tsvector** enables Postgres FTS for the BM25-lite half of hybrid search. The `ts` column is a generated stored column so it stays in sync automatically.
- **Composite index on `(user_id, created_at desc)`** for list views is more important than it looks — it's the difference between fast dashboard loads and full scans.

---

## Migration convention

- Migrations live in `apps/api/migrations/` as `YYYYMMDD_NNN_short_description.sql` (e.g. `20260722_001_initial.sql`) and are applied in filename order
- Idempotent where possible (`create ... if not exists`, `drop policy if exists`, `drop function if exists`)
- **Local:** `supabase start` from `apps/api/` applies nothing automatically — run each file against the local DB (`psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -f <file>`) or via Studio
- **Production:** applied manually, in order, through the Supabase dashboard SQL editor before deploying code that depends on them. They are therefore not recorded in Supabase's CLI migration history (`supabase migration list` is empty) — that is expected
- Each migration documents its rollback in a trailing `-- ROLLBACK` comment block
- Every schema change needs a CHANGELOG entry and a row in the migration log below

---

## What's not in the schema (deliberately)

- No `users` table of our own — Supabase Auth owns it (`auth.users`)
- No `sessions` table — Supabase Auth handles session state via JWT
- No `subscriptions` / `billing` tables — not in scope
- No `organizations` / `teams` — Phase 4+ if ever
- No soft-delete columns (yet) — hard delete via cascade for now; add if data retention becomes a concern

---

## Migration log

Migrations up to `20260804_001` are applied to the production project; later ones must be applied before deploying the code that ships with them (docs/DEPLOYMENT.md). Full reasoning for each is in the file's header comment and the matching CHANGELOG entry.

| Migration | Feature | Summary |
|---|---|---|
| `20260722_001_initial.sql` | FEAT-001 | Extensions, enums, the 5 core tables, indexes (incl. HNSW on `chunks.embedding`), RLS policies, `uploads`/`figures` buckets + policies. Verify with `verify_20260722_001.sql` |
| `20260722_002_grant_table_privileges.sql` | FEAT-001 | Table GRANTs that `001` omitted — RLS alone does not unlock a table a role has no base privilege on. `authenticated` gets only what its policies allow; `service_role` full CRUD; `anon` nothing |
| `20260724_001_hybrid_search_functions.sql` | FEAT-009 | `match_chunks_by_vector` and `match_chunks_by_fts` RPCs |
| `20260724_002_query_persistence_function.sql` | FEAT-012 | `create_query_turn` — atomic conversation/messages/citations write |
| `20260725_001_citation_marker_column.sql` | FEAT-026 | `citations.marker` — persists which `[N]` a citation came from |
| `20260725_002_query_persistence_function_marker.sql` | FEAT-026 | `create_query_turn` writes `marker` |
| `20260727_001_citation_document_mime_type.sql` | FEAT-020 | Retrieval functions return `document_mime_type` for format-aware citation display |
| `20260731_001_embedding_provider_fallback.sql` | Embedding fallback | `embedding_provider` enum + `chunks.embedding_provider`; provider-filtered `match_chunks_by_vector`; `distinct_embedding_providers` |
| `20260731_002_citation_persistence_defensive.sql` | Fix | Each citation insert in `create_query_turn` wrapped in its own exception block so one bad citation can't roll back a whole turn |
| `20260802_001_usage_counters.sql` | FEAT-024 | `usage_counters` table + `increment_usage_counter` for Postgres-backed daily rate limits |
| `20260802_002_citation_association_method.sql` | Fix | Retrieval functions return `association_method` |
| `20260803_001_citation_verdict_unverified.sql` | FEAT-028 | Adds `'unverified'` to the `verdict` enum |
| `20260804_001_avatars_bucket.sql` | FEAT-032 | Public-read `avatars` bucket with owner-scoped write policies |
| `20261006_001_fts_any_term_matching.sql` | Fix | `match_chunks_by_fts` matches any question term instead of requiring all of them (`websearch_to_tsquery` ANDed every term, so natural-language questions rarely matched); adds `fts_any_term_query` |

Account deletion (FEAT-035) needed no migration: all 6 user-scoped tables already cascade on `auth.users` deletion, and Storage cleanup is done in application code.
