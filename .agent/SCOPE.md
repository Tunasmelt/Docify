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
- Background-task durability (timeouts, dead-letter, crash recovery) deferred to Phase 5 — FastAPI BackgroundTasks has no durability guarantee; a genuinely hung or crashed task is currently invisible beyond ordinary log output. Revisit as part of deploy architecture, not patched incrementally here. (Flagged by Codex review of FEAT-007, 2026-07-23 — confirmed real, deliberately not fixed in that pass.)
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
- [ ] Rate-limiting on `/ingest` and `/query` to protect free tiers
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
