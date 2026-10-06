# Scope

Source of truth for what is and is not in scope, per phase. Check here before declaring anything done, and before adding new work. This file states current status; the history behind each item lives in `CHANGELOG.md` and `.agent/FEATURES.md`.

**Rule:** anything not in this file is not in scope. New ideas go to HANDOFF.md `## Agent Suggestions` first, get promoted to FEATURES.md `## Considered Suggestions`, then get added here.

---

## Phase 0 — Setup
**Status:** complete

### In scope
- [x] Ground-truth docs drafted (AGENT.md + .agent/*)
- [x] Monorepo initialized (`apps/web`, `apps/api`, `docs`, `.agent`)
- [x] Git repo + `.gitignore` + feature-branch convention
- [x] Supabase project created (Auth + Postgres + Storage + pgvector)
- [x] Voyage AI and Gemini API keys in env (no Anthropic key — generation moved to Gemini before any model code existed)
- [x] Render service linked to `apps/api` (Docker runtime)
- [x] Vercel project `docify-web` linked to `apps/web` (git-linked, auto-deploy on push)
- [x] `.env.example` committed in both apps

### Explicitly out of scope
- Custom domain, monitoring/observability → Phase 5

---

## Phase 1 — Ingestion Pipeline
**Status:** complete

### In scope
- [x] `/ingest` accepts PDF, DOCX, PPTX, HTML; `/reindex` re-runs the pipeline for an existing document
- [x] Parses into typed elements (text, table, figure, heading, list, caption) — pdfplumber / python-docx / python-pptx / selectolax (FEAT-027, replacing Docling)
- [x] Chunks embedded with Voyage, with a per-batch Gemini `embedding-2` fallback recorded in `chunks.embedding_provider`
- [x] Figures rendered to Supabase Storage as PNGs
- [x] Chunks written with `document_id`, `user_id`, embedding, page/slide, element type, bbox
- [x] Document status transitions (uploaded → parsing → embedded → ready | failed)
- [x] Tenant isolation: RLS on all tables + explicit `user_id` scoping on every service-role query
- [x] Failures write `documents.error`, set `failed`, and leave no partial chunks
- [x] OCR fallback for low-yield PDF pages (Gemini → OCR.space → Tesseract, FEAT-017)

### Explicitly out of scope
- Streaming upload progress (the frontend polls status after upload instead)
- Deduplication of identical documents
- Worker-pool/queue execution → tracked under Phase 5 "Production job execution"

---

## Phase 2 — Retrieval + Generation
**Status:** complete

### In scope
- [x] `/query` accepts a question + `document_ids[]`
- [x] Query embedded once per embedding provider present in scope
- [x] Vector search over `chunks`, scoped by explicit `user_id`/`document_id` parameters (the service-role client bypasses RLS)
- [x] Top-k retrieval, configurable k (default 8)
- [x] Gemini 3.6 Flash generation with inline `[N]` citations
- [x] Response returns answer + cited chunks with source metadata
- [x] Hybrid search: N provider-partitioned vector lists + Postgres FTS, fused with RRF
- [x] Opt-in Voyage rerank-2.5 (default off)
- [x] Conversation memory in the prompt (last 5 turns), plus follow-up questions rewritten into standalone search queries for retrieval
- [x] SSE streaming (`/query/stream`) with keepalives and disconnect handling

### Explicitly out of scope
- Multi-document synthesis with an attribution matrix

---

## Phase 3 — Frontend + Auth
**Status:** complete

### In scope
- [x] Supabase Auth: email/password + Google OAuth (OAuth's live consent-screen click-through is still unverified — see MEMORY.md §Open questions)
- [x] Protected-route middleware
- [x] Upload page with drag-drop, progress state, document list
- [x] Chat page: question input, streaming answers, inline citation chips, copy, timestamps, stop/regenerate
- [x] Citation chip → source panel with chunk text and figure image; "Open page N" shows a PDF page with the cited area highlighted, and DOCX/PPTX/HTML open the cited slide or section as text with the cited passage highlighted
- [x] Document list: filter, rename (`PATCH /documents/{id}`, 2026-10-06) and delete
- [x] Conversation rename + delete
- [x] Empty states, loading skeletons, error boundaries
- [x] Mobile-responsive layout

### Explicitly out of scope
- Team/organization multi-user
- Billing / usage-limits UI

---

## Phase 4 — Citation Verification + Polish
**Status:** complete

### In scope
- [x] Post-generation verifier (Gemini 3.5 Flash-Lite) per cited claim → supported | partial | unsupported, plus `unverified` when verification could not run (FEAT-028)
- [x] `unsupported` citations dropped and their markers stripped; `unverified` kept with a distinct indicator
- [x] Every verdict (including dropped ones) stored in `citations` for audit, and included in data export
- [x] Reranker (Voyage rerank-2.5), opt-in
- [x] OCR fallback chain (FEAT-017)
- [x] DOCX/PPTX/HTML ingestion with format-aware citation display (FEAT-020)
- [x] Conversation memory (FEAT-019)
- [x] Settings: profile, avatar, email change (password-gated), password, sign-out-other-sessions, preferences, data export, account deletion (FEAT-032–035)

### Explicitly out of scope
- Strategy selector UI (v2)
- Fine-tuning any model

---

## Phase 5 — Deploy + Portfolio Polish
**Status:** in progress

### In scope
- [x] Render prod deploy of `apps/api` — `https://docify-api.onrender.com`; `GET /health` reports the running commit
- [x] Vercel prod deploy of `apps/web` — `https://docify-web-steel.vercel.app` (2026-08-09)
- [x] Landing page (FEAT-023, public `/`)
- [ ] Demo video/GIF and a "try with a sample document" flow on the landing page
- [x] README at repo root: overview, screenshots, tech stack, quick start
- [ ] Custom domain (if one is available)
- [x] CI: `pytest` (against a local Supabase stack), `next lint`, `tsc` and `next build` on every PR (`.github/workflows/ci.yml`). Playwright e2e is not run in CI
- [ ] Error tracking (Sentry free tier, FEAT-025). **Code done** 2026-10-06 (API + web, inactive until a DSN is set); still needs the Sentry projects created and DSNs added on Render/Vercel (docs/DEPLOYMENT.md §Monitoring)
- [ ] Uptime monitor (UptimeRobot free) against `GET /health`. Setup steps in docs/DEPLOYMENT.md §Monitoring; needs an UptimeRobot account (no code change)
- [x] **Production job execution for `/ingest`** (2026-10-06: Postgres job queue, services/ingest_queue.py). Status of each facet:
  - [x] **Durability** — running jobs heartbeat; a job whose worker died is claimed again within ~2 minutes and resumes. Transient failures retry automatically (60s, then 300s; 3 attempts). The `GET /documents` reaper remains for documents with no job (pre-queue).
  - [x] **Rate limiting** — per-user and global per-minute limits (in memory), daily limits in Postgres (FEAT-024). In-memory counters are only correct while the API runs as a single instance; move to a shared store (Redis via slowapi `storage_uri=`) before scaling out. There is no single budget spanning both `/ingest` and `/query`.
  - [x] **Per-user concurrency cap** (2026-10-06) — at most 2 documents processing per user (`MAX_CONCURRENT_INGESTS_PER_USER`); `/ingest` and `/reindex` return `429 TOO_MANY_PROCESSING` beyond that. Counted in Postgres, so it holds across restarts and instances.
  - [x] **Queue, global cap, time limit, document limits** — one worker thread per API process; at most `INGEST_MAX_CONCURRENT_JOBS` (default 1) jobs run across all users and instances. 20-minute time limit per attempt, checked between stages and OCR'd pages (cooperative: an OCR call in flight can overrun by its own timeout). Documents over 50 MB, 300 pages/slides, or 30 pages needing OCR are refused before the expensive work starts.
  - [x] **Reindex keeps old chunks** — new chunks are staged and swapped in atomically; a failed reindex leaves the old ones live.
  - [ ] Still open: the worker shares the API process (a hard kill on timeout, or OOM isolation, would need a subprocess or separate worker service), and a reindex still cascades away citations that pointed at the old chunks.

### Explicitly out of scope
- Marketing site beyond the landing page
- Analytics beyond basic pageviews
- Paid infrastructure

---

## Locked out of scope for the entire project
- Fine-tuning any embedding, generation, or verification model
- An ML-trained custom parser (the heuristic FEAT-027 parser trains nothing and is in scope)
- Real-time collaboration
- Mobile native app
- Public API / developer platform
- Billing / payments / subscriptions
- Anything requiring paid infrastructure
