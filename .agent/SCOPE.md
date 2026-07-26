# Scope

Source of truth for what is and is not in scope, per phase. The agent checks here before declaring anything done, and before adding any new work.

**Rule:** Anything not in this file is not in scope. New ideas go to HANDOFF.md `## Agent Suggestions` first, get promoted to FEATURES.md `## Considered Suggestions`, then get added here.

---

## Phase 0 — Setup
**Status:** in-progress

### In scope
- [x] Ground-truth docs drafted (AGENT.md + .agent/*)
- [ ] Monorepo initialized (`apps/web`, `apps/api`, `docs`, `.agent`)
- [ ] Git repo + `.gitignore` + `main` branch protection convention
- [ ] Supabase project created (Auth + Postgres + Storage + pgvector enabled)
- [ ] Voyage AI account + API key stored in env
- [ ] Anthropic API key stored in env
- [ ] Gemini API key stored in env
- [ ] Docling installed and importable in `apps/api`
- [ ] Render service linked to `apps/api`
- [ ] Vercel project linked to `apps/web`
- [ ] `.env.example` committed at both apps

### Explicitly out of scope
- CI/CD pipelines (Phase 5)
- Custom domain (Phase 5)
- Monitoring / observability (Phase 5)

### Dependencies
- All API keys must exist before Phase 1 begins

---

## Phase 1 — Ingestion Pipeline
**Status:** planned

### In scope
- [ ] `/ingest` endpoint accepts PDF upload
- [ ] Docling parses PDF into typed elements (text, table, figure, heading)
- [ ] Text chunks embedded with Voyage
- [ ] Figures rendered to Supabase Storage as PNGs
- [ ] Chunks written to `chunks` table with `document_id`, `user_id`, embedding, metadata (page number, element type, source coords)
- [ ] Document row created in `documents` table with status transitions (uploaded → parsing → embedded → ready)
- [ ] Multi-tenant isolation enforced via RLS from first insert
- [ ] Failure handling: parse failures write to `documents.error` and set status to `failed`, no partial-ingest data left in `chunks`

### Explicitly out of scope
- Background-task durability, worker-pool/queue architecture, and rate-limiting — three facets of one decision (FastAPI `BackgroundTasks` is deliberately not a production job system for Phase 1). Consolidated under Phase 5's "Production job execution" entry below rather than scattered across phases — see that entry for the full reasoning and evidence.
- OCR fallback for scanned PDFs (Phase 4 — add Gemini Flash route only when Docling low-confidence pages appear in real usage)
- DOCX/PPTX/HTML inputs (Phase 4)
- Streaming upload progress to frontend (Phase 3)
- Deduplication of identical documents (Phase 4)

### Dependencies
- Phase 0 complete
- SCHEMA.md finalized

---

## Phase 2 — Retrieval + Generation
**Status:** planned

### In scope
- [ ] `/query` endpoint accepts natural-language question + `document_id` or `document_ids[]`
- [ ] Query embedded with Voyage
- [ ] Vector search over `chunks` (cosine similarity, RLS-filtered by `user_id`)
- [ ] Top-k retrieval with configurable k (default 8)
- [ ] Claude Sonnet called with retrieved chunks in context, prompted to cite chunk IDs inline as `[1]`, `[2]`, etc.
- [ ] Response returns answer text + array of cited chunks with source metadata (page, element type, doc name)
- [ ] Basic hybrid search — BM25 via Postgres FTS layered onto vector search with RRF merge

### Explicitly out of scope
- Reranker (Phase 4)
- Conversation memory / follow-up context (Phase 3)
- Streaming responses (Phase 3)
- Multi-document synthesis with attribution matrix (Phase 4)

### Dependencies
- Phase 1 complete
- At least 3 real test documents ingested end-to-end

---

## Phase 3 — Frontend + Auth
**Status:** planned

### In scope
- [ ] Supabase Auth wired: email/password + Google OAuth
- [ ] Protected routes middleware in Next.js
- [ ] Upload page with drag-drop, progress state, document list
- [ ] Chat page with question input, streaming answer display, inline citation chips
- [ ] Citation chip click → source panel opens showing chunk + page image if available
- [ ] Document list page: filter, delete, rename
- [ ] Empty states, loading skeletons, error boundaries
- [ ] Mobile-responsive layout

### Explicitly out of scope
- User settings page beyond auth basics (Phase 4)
- Team/organization multi-user (Phase 4+)
- Billing / usage limits UI (out of portfolio scope entirely)

### Dependencies
- Phase 2 complete — `/query` returns real citations

---

## Phase 4 — Citation Verification + Polish
**Status:** planned

### In scope
- [ ] Post-generation verifier pass: Claude Haiku called per cited claim with `(claim, cited_chunk) → supported | partial | unsupported` verdict
- [ ] Unsupported claims stripped or flagged before returning to user
- [ ] Verifier verdict + reasoning stored in `citations` table for audit
- [ ] Reranker step added to retrieval (Voyage rerank-2 or self-hosted cross-encoder)
- [ ] OCR fallback via Gemini Flash for low-confidence Docling pages
- [ ] DOCX/PPTX/HTML ingestion (via Docling's native support)
- [ ] Conversation memory (previous Q&A in same session as context)
- [ ] Streaming responses via SSE

### Explicitly out of scope
- Strategy selector UI (v2 — after v1 shipped)
- Fine-tuning any model (never in scope for this project)

### Dependencies
- Phase 3 complete + real usage feedback

---

## Phase 5 — Deploy + Portfolio Polish
**Status:** planned

### In scope
- [ ] Vercel prod deploy of `apps/web`
- [ ] Render prod deploy of `apps/api` with env vars + health check
- [ ] Custom domain (if user has one)
- [ ] Landing page with demo video / gif and "try with sample doc" flow
- [ ] README.md at repo root: architecture diagram, tech decisions, how to run locally
- [ ] **Production job execution for `/ingest` (and `/query` once built)** — FastAPI `BackgroundTasks` is not a production job system: no queue, no worker pool, no timeout/backpressure, no per-user concurrency cap, and a hung or crashed task is invisible beyond ordinary log output (a process restart silently loses in-flight work). Three facets of the same underlying decision, tracked together here rather than as separate scattered bullets across phases:
  - **Durability** — timeouts, dead-letter handling, crash recovery for in-flight ingests
  - **Worker-pool/queue architecture** — move heavy Docling/Voyage work off the request-serving process entirely
  - **Rate-limiting** on `/ingest` and `/query`, to protect free-tier quotas from concurrent or abusive load
  Evidence: `.agent/reviews/2026-07-23-perf.md` (measured 86.55s parse time for one 11-page fixture locally; no per-stage timing exists to explain that number after the fact) and `.agent/reviews/2026-07-23-efficiency.md` (reconfirms this is unchanged as of FEAT-008; separately assesses Parser/Embedder process-lifetime reuse as an independent, smaller change that does *not* need to wait for this).

  **Update (2026-07-26, FEAT-017's 3-tier OCR fallback audit):** this gap is now measurably worse for ingest specifically, not just unchanged. `Parser.parse()`'s per-page OCR step can make up to 3 sequential network/subprocess calls (Gemini, then OCR.space, then a local Tesseract invocation) with no shared upper bound across the whole tier chain — each tier now has its own explicit per-call timeout (60s: `OCR_TIMEOUT_MS`, added the same day this was found), closing the "any one call hangs forever" version of the gap, but a page that genuinely exhausts all three tiers slowly (three real ~60s timeouts back to back) can add up to ~3 minutes to one page's processing before this project's own worker-pool/timeout-at-the-job-level work (above) ever gets built. Confirmed live: neither Gemini nor Tesseract had *any* timeout before this fix (Gemini: `genai.Client()` with no `http_options.timeout` passes `timeout=None` straight through to httpx, which treats that as "wait forever," confirmed against the installed SDK source; Tesseract: `pytesseract.image_to_string`'s own default `timeout=0` skips `subprocess.communicate()`'s timeout entirely, confirmed against the installed pytesseract source) — only OCR.space had one from the start. Per-call timeouts are a real, sufficient fix for the "one call hangs the whole ingest forever" failure mode; the "three real timeouts stack up sequentially, worst case ~3 minutes on one bad page" failure mode is a smaller, bounded version of the same underlying gap this entry already tracks, not a new one — noted here rather than left implicit, per this feature's own audit.
- [ ] Sentry or equivalent lightweight error tracking (free tier)
- [ ] Basic uptime monitor (UptimeRobot free)

### Explicitly out of scope
- Marketing site beyond landing
- Analytics beyond basic pageview
- Paid infra migration

### Dependencies
- Phase 4 complete

---

## Locked out-of-scope for the entire project
- Fine-tuning any embedding, generation, or verification model
- Building a custom parser to replace Docling
- Real-time collaboration features
- Mobile native app
- Public API / developer platform
- Billing / payments / subscriptions
- Anything requiring paid infra beyond free-tier expiry
