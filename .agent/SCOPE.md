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
- [x] DOCX/PPTX/HTML ingestion (via Docling's native support) — FEAT-020, 2026-07-27. Note: the rest of this Phase 4 checklist (verifier, reranker, OCR fallback, conversation memory) is also already built (FEAT-011/018/017/019) but wasn't checked off here when each shipped — left as-is, out of this task's scope to reconcile the whole list.
- [x] Conversation memory (previous Q&A in same session as context) — FEAT-019, 2026-07-27
- [x] Streaming responses via SSE — FEAT-016, 2026-07-27 (`POST /query/stream`; filed under Phase 2 in FEATURES.md, not this Phase 4 list, since it's a direct `/query` extension — checked off here too since this line still names it)

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
- [x] Render prod deploy of `apps/api` with env vars + health check — 2026-07-27. Live at `https://docify-api.onrender.com`, real health check and real authenticated request both verified (CHANGELOG.md). **Directly confirms the "Production job execution" risk below, not just predicts it:** a real `/ingest` call OOM-crashed the free-tier instance mid-Docling-parse — the crashed background task left its document silently stuck in `parsing` forever, exactly the "a process restart silently loses in-flight work" failure mode this entry already named before any real deploy existed to prove it. Not fixed in this pass.
- [ ] Custom domain (if user has one)
- [ ] Landing page with demo video / gif and "try with sample doc" flow
- [ ] README.md at repo root: architecture diagram, tech decisions, how to run locally
- [ ] **Production job execution for `/ingest` (and `/query` once built)** — FastAPI `BackgroundTasks` is not a production job system: no queue, no worker pool, no timeout/backpressure, no per-user concurrency cap, and a hung or crashed task is invisible beyond ordinary log output (a process restart silently loses in-flight work). Three facets of the same underlying decision, tracked together here rather than as separate scattered bullets across phases:
  - [x] **Durability — closed 2026-08-02 via a lazy/reactive approach, not a real worker queue (FEAT-024 follow-up).** `GET /documents` (`routes/documents.py`) opportunistically reaps any of the requesting user's own documents still stuck in `'parsing'`/`'embedded'` past a 30-minute threshold to `'failed'`, and `POST /reindex/{document_id}` (`routes/ingest.py`) lets the user recover it (or any document, for other reasons — a Voyage-fallback re-embed, a retry of FEAT-017's OCR chain) by re-running the pipeline from the file already in Storage. **Explicit statement of the tradeoff this represents, not left implicit:** this is NOT a real crash-recovery system — there is no automatic retry, no dead-letter queue, no proactive detection (a document stays visibly stuck until a user's own `GET /documents` call happens to run past the threshold), and the 30-minute threshold is a documented, not proven-optimal, judgment call (`STUCK_DOCUMENT_THRESHOLD_SECONDS`, `routes/ingest.py`). It is deliberately the SMALLEST fix that closes the real, proven gap (a document stuck forever with **zero** path to recovery) without building the worker-pool/queue infrastructure below. **Why this tradeoff is acceptable at this project's scale:** a solo-dev portfolio project running one Render free-tier instance has no real concurrent-user load to make "a user has to reload their document list to trigger recovery" a meaningful UX cost, and the actual failure this closes (the FEAT-027-era Render OOM crash) is now independently far less likely to recur (parser rewrite: ~160MB peak vs. the old 1144.6MB) — this is a durability *backstop* for a rare event, not a load-bearing system for common ones. Revisit with a real queue/worker system (the entry below) if either changes: real concurrent traffic, or a failure mode this reactive approach doesn't cover (e.g. a task hung, not crashed — still consuming resources indefinitely with no timeout of its own; not addressed here).
    Real regression test, not a hand-crafted row: `apps/api/tests/test_reindex.py`'s `test_reaper_and_reindex_recover_a_document_whose_pipeline_was_really_killed` interrupts a real call to `run_ingest_pipeline()` mid-flight via a `BaseException` (bypassing the function's own `except Exception` cleanup entirely, the same way a real SIGKILL/OOM-kill does), confirms the document is genuinely stuck at `'parsing'` with no error message (proving the interruption, not a handled failure, actually happened), then drives the real `GET /documents` and `POST /reindex/{id}` routes end-to-end to confirm full recovery to `'ready'`.
  - **Worker-pool/queue architecture — still open, not addressed by durability's lazy-reaper fix above.** Heavy Docling/Voyage work still runs on the request-serving process; no timeout/backpressure/per-user concurrency cap exists for a task that's genuinely hung (not crashed) rather than dead.
  - [x] **Rate-limiting** on `/ingest`, `/reindex`, and `/query`, to protect free-tier quotas from concurrent or abusive load — done 2026-07-28, FEAT-024 (`apps/api/rate_limit.py`, `routes/ingest.py`/`routes/query.py`); extended 2026-08-02 (FEAT-024 follow-up). `slowapi`, in-memory storage (no `storage_uri=`) for per-minute limits — a deliberate, stated tradeoff, not an unexamined default: this project runs a single Render instance with no Redis anywhere else in the stack, and adding one just for this would be real new infrastructure for a problem that doesn't need it yet. **Known, accepted limitation, logged here explicitly:** per-minute limits reset on every redeploy/cold-start restart (acceptable — a 15-minute idle gap already exceeds any per-minute window, so a restart-induced reset and a legitimate window rollover are indistinguishable in practice), and this in-memory approach stops being correct the moment this service ever runs as more than one instance, since each instance would track its own separate counters, silently multiplying the effective limit by instance count. Revisit with a shared store (Redis via `storage_uri=`) if either changes. Keyed per-user (`request.state.user_id`, JWT-verified — never IP) for the per-user limits, values derived from real measured vendor ceilings (Voyage's real 3 RPM, Gemini's real 20/day for `gemini-2.5-flash` specifically — `.agent/MEMORY.md`), not round numbers — see `API_CONTRACT.md`'s `/ingest`/`/reindex`/`/query` entries for the exact numbers and reasoning.
    **2026-08-02 additions, closing the two real gaps the original per-user-only design left named-but-open:**
    - **A second, GLOBAL per-minute limit** (`3/minute`, keyed by a fixed value, not `user_id`) on `/ingest`+`/reindex` — closes the specific gap the per-user design structurally could not: N different users, each individually within their own limit, collectively exceeding the true account-level vendor ceiling.
    - **Daily limits moved to Postgres** (`usage_counters` table, migration `20260802_001_usage_counters.sql`, `rate_limit.py`'s `check_daily_limit`) — replacing in-memory storage specifically for the DAILY (not per-minute) limits. Real reason this one *did* need fixing, unlike the per-minute ones: a 15-minute-or-longer spin-down is a non-issue for a per-minute window, but a user could ride out one or more spin-downs over a day and get a fresh daily budget on each restart, silently multiplying their real quota against the shared vendor ceiling. Proven durable via a real test that clears all in-memory state mid-quota and confirms the Postgres count survives (`test_daily_limit_count_survives_in_memory_state_being_cleared`, `test_rate_limit.py`).
    **Still, deliberately, not fully closed** (unchanged scope from the original feature, stated explicitly rather than silently left ambiguous): no single counter spans `/ingest`+`/reindex` AND `/query` together — a user hammering both simultaneously could still, in the worst case, exceed the shared vendor budget alone. That specific cross-endpoint gap was never in this feature's scope (only within `/ingest`+`/reindex` was), and remains open.
  Evidence: `.agent/reviews/2026-07-23-perf.md` (measured 86.55s parse time for one 11-page fixture locally under the old Docling parser; no per-stage timing exists to explain that number after the fact) and `.agent/reviews/2026-07-23-efficiency.md` (reconfirms this is unchanged as of FEAT-008; separately assesses Parser/Embedder process-lifetime reuse as an independent, smaller change that does *not* need to wait for this). **Superseded 2026-08-01 by FEAT-027:** the 86.55s figure and the whole "Docling is the parser" framing are historical — real parse time under the current pdfplumber-based parser is ~1-2s; see `.agent/FEATURES.md`'s FEAT-027 entry for current numbers. `STUCK_DOCUMENT_THRESHOLD_SECONDS`'s own 30-minute derivation (above) is based on the CURRENT parser's real numbers plus the OCR-chain figure below, not the stale 86.55s one.

  **Update (2026-07-26, FEAT-017's 3-tier OCR fallback audit):** this gap is now measurably worse for ingest specifically, not just unchanged. `Parser.parse()`'s per-page OCR step can make up to 3 sequential network/subprocess calls (Gemini, then OCR.space, then a local Tesseract invocation) with no shared upper bound across the whole tier chain — each tier now has its own explicit per-call timeout (60s: `OCR_TIMEOUT_MS`, added the same day this was found), closing the "any one call hangs forever" version of the gap, but a page that genuinely exhausts all three tiers slowly (three real ~60s timeouts back to back) can add up to ~3 minutes to one page's processing before this project's own worker-pool/timeout-at-the-job-level work (above) ever gets built. Confirmed live: neither Gemini nor Tesseract had *any* timeout before this fix (Gemini: `genai.Client()` with no `http_options.timeout` passes `timeout=None` straight through to httpx, which treats that as "wait forever," confirmed against the installed SDK source; Tesseract: `pytesseract.image_to_string`'s own default `timeout=0` skips `subprocess.communicate()`'s timeout entirely, confirmed against the installed pytesseract source) — only OCR.space had one from the start. Per-call timeouts are a real, sufficient fix for the "one call hangs the whole ingest forever" failure mode; the "three real timeouts stack up sequentially, worst case ~3 minutes on one bad page" failure mode is a smaller, bounded version of the same underlying gap this entry already tracks, not a new one — noted here rather than left implicit, per this feature's own audit. **This is the real number `STUCK_DOCUMENT_THRESHOLD_SECONDS`'s 30-minute reaper threshold (above) is derived against.**
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
