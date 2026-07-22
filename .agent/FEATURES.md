# Features

Canonical list of every feature — status, phase, files, tests. This is the **no-feature-loss registry**.

**Status values:** `planned` · `in-progress` · `complete` · `tested` · `locked`

Rule: a feature is not `complete` until acceptance criteria pass AND `/gap-check` reports no gaps AND `/feature-check` reports test coverage.

---

## Phase 0 — Setup

### [FEAT-000] Repo skeleton
**Phase:** 0
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/web/package.json` — Next.js 14 App Router boilerplate
- `apps/api/pyproject.toml` — FastAPI + Docling + Voyage + Gemini deps
- `.gitignore` — node_modules, .env, __pycache__, HANDOFF.md, .agent/logs, .agent/index.json
- `README.md` — root readme with quick-start
- `.env.example` at both app roots

**Tests:** —
**Acceptance criteria:**
- [ ] `pnpm install` succeeds in `apps/web`
- [ ] `uv sync` succeeds in `apps/api`
- [ ] `pnpm dev` starts Next.js on :3000
- [ ] `uvicorn apps.api.main:app --reload` starts FastAPI on :8000

**Run:**
- Web: `cd apps/web && pnpm dev`
- API: `cd apps/api && uvicorn main:app --reload`

**Changelog:** — (add on completion)

---

### [FEAT-001] Supabase project + initial migration
**Phase:** 0
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/migrations/20260722_001_initial.sql` — schema per SCHEMA.md
- `apps/api/db/client.py` — service-role client
**Tests:**
- `apps/api/tests/test_migrations.py` — verifies tables exist, RLS is enabled, policies present
**Acceptance criteria:**
- [ ] Migration applies cleanly to a fresh local Supabase instance
- [ ] All tables from SCHEMA.md exist with correct columns and types
- [ ] RLS is enabled on documents, chunks, conversations, messages, citations
- [ ] pgvector extension enabled, HNSW index on chunks.embedding
- [ ] Two storage buckets created: `uploads` (private), `figures` (private) with policies

**Run:**
- `supabase start` locally, then `supabase db push`
- Verify: `psql -h localhost -p 54322 -U postgres -d postgres -c "\dt"`

**Changelog:** —

---

## Phase 1 — Ingestion Pipeline

### [FEAT-002] `/health` endpoint
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/routes/health.py`
**Tests:**
- `apps/api/tests/test_health.py`
**Acceptance criteria:**
- [ ] GET /health returns `{status, version, timestamp}` with 200
- [ ] No auth required

**Run:**
- `curl http://localhost:8000/health`

---

### [FEAT-003] JWT auth middleware
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/middleware/auth.py`
- `apps/api/main.py` — wire middleware
**Tests:**
- `apps/api/tests/test_auth.py` — valid JWT, expired JWT, malformed JWT, missing JWT
**Acceptance criteria:**
- [ ] Non-`/health` routes reject requests without `Authorization: Bearer <jwt>` with 401
- [ ] Invalid signature returns 401
- [ ] Valid JWT attaches `user_id` to `request.state.user_id`
- [ ] JWT verification uses `SUPABASE_JWT_SECRET`

**Run:**
- `curl -H "Authorization: Bearer <token>" http://localhost:8000/documents`

---

### [FEAT-004] Docling parser service
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/parser.py`
**Tests:**
- `apps/api/tests/test_parser.py` — three fixture PDFs (clean digital, scanned, table-heavy)
**Acceptance criteria:**
- [ ] `Parser.parse(pdf_bytes) -> ParsedDocument` returns typed elements: text, heading, table, figure, caption, list
- [ ] Each element has: page_number, bbox (x0,y0,x1,y1), content, element_type
- [ ] Tables are extracted as markdown-formatted content
- [ ] Figures are returned as PIL Image objects for downstream storage
- [ ] Parse failures raise `ParseError` with the source page number

**Run:**
- `pytest apps/api/tests/test_parser.py -v`

---

### [FEAT-005] Chunker
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/chunker.py`
**Tests:**
- `apps/api/tests/test_chunker.py`
**Acceptance criteria:**
- [ ] Groups adjacent text/heading elements into chunks of ~500 tokens, respecting element boundaries (never splits a table row or a heading)
- [ ] Preserves metadata: page numbers, source element indices, element_type of primary element
- [ ] Each chunk has a stable `chunk_index` (ordinal within document)
- [ ] Figure elements produce their own chunks (one figure = one chunk, with caption prepended if adjacent)

**Run:**
- `pytest apps/api/tests/test_chunker.py -v`

---

### [FEAT-006] Voyage embedder wrapper
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/embedder.py`
- `.agent/api-docs/voyage.md` (via `/api-check voyage`)
**Tests:**
- `apps/api/tests/test_embedder.py` — mocked HTTP, real HTTP behind env flag
**Acceptance criteria:**
- [ ] `Embedder.embed(chunks) -> list[Vector]` handles text-only and text+image chunks
- [ ] Returns 1024-dim vectors from `voyage-multimodal-3.5`
- [ ] Batches API calls (max 128 inputs per call)
- [ ] Retries on 429 with exponential backoff (max 3 retries)
- [ ] Raises `EmbedError` on non-transient failures

**Run:**
- Before implementation: `/api-check voyage` and read `.agent/api-docs/voyage.md`

---

### [FEAT-007] `/ingest` endpoint
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Depends on:** FEAT-003, FEAT-004, FEAT-005, FEAT-006
**Files:**
- `apps/api/routes/ingest.py`
- `apps/api/db/queries.py` — document + chunk insert helpers
**Tests:**
- `apps/api/tests/test_ingest.py` — integration test with local Supabase
- `apps/api/tests/e2e/test_ingest_e2e.py`
**Acceptance criteria:**
- [ ] POST /ingest with valid body returns 202 + document_id
- [ ] storage_path prefix mismatch with JWT user_id returns 403
- [ ] Background task: download → parse → chunk → embed → insert → update status
- [ ] Failure at any stage sets documents.status = 'failed' with error message
- [ ] Multi-tenant isolation: user A cannot see user B's document via any query path

**Run:**
- Upload sample.pdf to Supabase Storage under `uploads/{user_id}/`
- `curl -X POST -H "Authorization: Bearer <jwt>" -d '{"storage_path":"...","filename":"...","mime_type":"application/pdf","size_bytes":123}' http://localhost:8000/ingest`

---

### [FEAT-008] `/documents` list + detail + delete
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/routes/documents.py`
**Tests:**
- `apps/api/tests/test_documents.py`
**Acceptance criteria:**
- [ ] GET /documents returns paginated list scoped to JWT user
- [ ] GET /documents/{id} returns 404 for another user's doc
- [ ] DELETE /documents/{id} cascades to chunks, conversations reference, storage files
- [ ] DELETE while status='parsing' returns 409

**Run:**
- `curl -H "Authorization: Bearer <jwt>" http://localhost:8000/documents`

---

## Phase 2 — Retrieval + Generation

### [FEAT-009] Retriever service (hybrid + RRF)
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/retriever.py`
**Tests:**
- `apps/api/tests/test_retriever.py`
**Acceptance criteria:**
- [ ] `Retriever.retrieve(question, document_ids, user_id, k) -> list[Chunk]`
- [ ] Runs vector search (cosine) and BM25 (Postgres FTS) in parallel
- [ ] Merges via Reciprocal Rank Fusion (k=60 default)
- [ ] Returns top-k with metadata: chunk_id, content, page, document_name, element_type
- [ ] user_id is included in every SQL WHERE clause explicitly

**Run:**
- `pytest apps/api/tests/test_retriever.py -v`

---

### [FEAT-010] Gemini generator wrapper
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/generator.py`
- `.agent/api-docs/gemini.md` (via `/api-check gemini`)
**Tests:**
- `apps/api/tests/test_generator.py`
**Acceptance criteria:**
- [ ] `Generator.generate(question, chunks) -> GenerateResult` returns answer text + parsed citation markers
- [ ] System prompt instructs Gemini 3.6 Flash to cite chunk IDs inline as `[N]`
- [ ] Multimodal — figure chunks pass their image content to Gemini
- [ ] Returns metadata: model, input_tokens, output_tokens, latency_ms

**Run:**
- Before implementation: `/api-check gemini`

---

### [FEAT-011] Citation verifier
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/verifier.py`
**Tests:**
- `apps/api/tests/test_verifier.py` — supported / partial / unsupported fixtures
**Acceptance criteria:**
- [ ] `Verifier.verify(claim, chunk) -> Verdict{verdict, quote}` uses Gemini 3.5 Flash-Lite
- [ ] Verdict enum: supported | partial | unsupported
- [ ] Returns the supporting quote from the source (or null if unsupported)
- [ ] Batches verifications per generate call

**Run:**
- `pytest apps/api/tests/test_verifier.py -v`

---

### [FEAT-012] `/query` endpoint
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Depends on:** FEAT-009, FEAT-010, FEAT-011
**Files:**
- `apps/api/routes/query.py`
**Tests:**
- `apps/api/tests/test_query.py`
- `apps/api/tests/e2e/test_query_e2e.py`
**Acceptance criteria:**
- [ ] POST /query returns 200 with answer + citations per API_CONTRACT.md
- [ ] Unsupported citations are dropped from response, markers stripped from answer text
- [ ] Creates conversation + message + citations rows atomically
- [ ] Continuing an existing conversation appends messages correctly
- [ ] Cross-tenant document_ids in request → 403

**Run:**
- `curl -X POST -H "Authorization: Bearer <jwt>" -d '{"question":"...","document_ids":["..."]}' http://localhost:8000/query`

---

## Phase 3 — Frontend + Auth

### [FEAT-013] Next.js app shell + Supabase Auth
**Phase:** 3
**Status:** planned
**Owner:** claude-design + gemini (auth wiring)
**Files:**
- `apps/web/app/layout.tsx`
- `apps/web/app/(auth)/login/page.tsx`
- `apps/web/app/(auth)/signup/page.tsx`
- `apps/web/middleware.ts`
- `apps/web/lib/supabase/browser.ts` + `server.ts`
**Tests:**
- `apps/web/e2e/auth.e2e.ts`
**Acceptance criteria:**
- [ ] Login with email/password works
- [ ] Signup creates a Supabase Auth user
- [ ] Google OAuth flow works
- [ ] Protected routes redirect unauthenticated users to /login
- [ ] Layout has navigation shell (sidebar + top bar)

---

### [FEAT-014] Upload UI + document list
**Phase:** 3
**Status:** planned
**Owner:** claude-design
**Files:**
- `apps/web/app/(app)/documents/page.tsx`
- `apps/web/components/documents/upload-zone.tsx`
- `apps/web/components/documents/document-card.tsx`
- `apps/web/lib/api/client.ts`
**Tests:**
- `apps/web/e2e/upload.e2e.ts`
**Acceptance criteria:**
- [ ] Drag-drop or click-to-select PDF
- [ ] Upload progress bar
- [ ] Document appears in list immediately with status 'parsing'
- [ ] Status updates via polling (initial: every 2s while not ready/failed, max 60s)
- [ ] Delete confirmation modal
- [ ] Empty state when no docs
- [ ] Error state when upload fails

---

### [FEAT-015] Chat UI + citation source panel
**Phase:** 3
**Status:** planned
**Owner:** claude-design
**Files:**
- `apps/web/app/(app)/chat/[conversation_id]/page.tsx`
- `apps/web/components/chat/message-bubble.tsx`
- `apps/web/components/chat/citation-chip.tsx`
- `apps/web/components/chat/source-panel.tsx`
- `apps/web/components/chat/question-input.tsx`
**Tests:**
- `apps/web/e2e/chat.e2e.ts`
**Acceptance criteria:**
- [ ] Question input at bottom, messages scroll above
- [ ] Assistant messages render inline citation chips
- [ ] Click citation → source panel opens with chunk content + page image (if figure)
- [ ] Verdict-based citation styling (supported = solid, partial = dashed warning, no unsupported shown)
- [ ] Loading state during retrieval + generation
- [ ] Auto-scroll to newest message
- [ ] Mobile-responsive (source panel becomes bottom sheet)

---

## Phase 4 — Polish (see SCOPE.md for full list)

Features here are placeholders until Phase 3 ships. Do not start unless Phase 3 is `complete`.

- [FEAT-016] Streaming responses via SSE — planned
- [FEAT-017] OCR fallback via Gemini Flash — planned
- [FEAT-018] Reranker step — planned
- [FEAT-019] Conversation memory in prompt — planned
- [FEAT-020] DOCX/PPTX ingestion — planned

---

## Phase 5 — Deploy (see SCOPE.md for full list)

- [FEAT-021] Vercel prod deploy — planned
- [FEAT-022] Render prod deploy — planned
- [FEAT-023] Landing page + demo — planned
- [FEAT-024] Rate limiting — planned
- [FEAT-025] Error tracking (Sentry free) — planned

---

## Considered Suggestions

Promoted from HANDOFF.md `## Agent Suggestions` inbox after review. Not yet scheduled into a phase.

*(empty — populate as suggestions are promoted)*
