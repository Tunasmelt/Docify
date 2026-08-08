# Scope

Source of truth for what is and is not in scope, per phase. The agent checks here before declaring anything done, and before adding any new work.

**Rule:** Anything not in this file is not in scope. New ideas go to HANDOFF.md `## Agent Suggestions` first, get promoted to FEATURES.md `## Considered Suggestions`, then get added here.

**Reconciled 2026-08-07** (docs-reconciliation pass) — this file was found frozen at project
inception: every phase status and nearly every checkbox below was still exactly as drafted in
Phase 0, even though Phases 0-4 are all actually complete and self-contradicted Phase 5's own
entries (which document a real, live production backend deploy) elsewhere in this same file.
Reconciled against real evidence (git history, live code, FEATURES.md) below — see each phase's
own note for what changed and why.

---

## Phase 0 — Setup
**Status:** complete

### In scope
- [x] Ground-truth docs drafted (AGENT.md + .agent/*)
- [x] Monorepo initialized (`apps/web`, `apps/api`, `docs`, `.agent`)
- [x] Git repo + `.gitignore` + `main` branch protection convention
- [x] Supabase project created (Auth + Postgres + Storage + pgvector enabled)
- [x] Voyage AI account + API key stored in env
- [ ] ~~Anthropic API key stored in env~~ — **never needed.** Generation/verification switched
  from Claude to Gemini on day one (commit `39f4ea7`, 2026-07-22, "docs: switch generation +
  verification from Claude to Gemini" — before any model-calling code existed). No
  `ANTHROPIC_API_KEY` reference exists anywhere in the real codebase. Struck rather than left
  as a permanently-open checkbox implying it's still needed.
- [x] Gemini API key stored in env
- [ ] ~~Docling installed and importable in `apps/api`~~ — **true when written, false now.**
  Docling was installed and used through FEAT-004; removed entirely 2026-08-01 (FEAT-027) after
  its real memory cost (1144.6MB peak parse) OOM-crashed `/ingest` on Render's free tier.
  Replaced by pdfplumber/python-docx/python-pptx/selectolax. Struck rather than checked, since
  checking it would assert something no longer true.
- [x] Render service linked to `apps/api` — live in production, `https://docify-api.onrender.com`
  (Phase 5)
- [ ] Vercel project linked to `apps/web` — **unconfirmed from this environment.** No evidence
  either way is visible from the repo/local dev setup alone (this is an external Vercel-account
  fact, not something git history or code can confirm). Left unchecked rather than guessed;
  Phase 5's own "Vercel prod deploy" item is separately, correctly still unchecked.
- [x] `.env.example` committed at both apps — `apps/web/.env.example`, `apps/api/.env.example`
  both exist

### Explicitly out of scope
- CI/CD pipelines (Phase 5)
- Custom domain (Phase 5)
- Monitoring / observability (Phase 5)

### Dependencies
- All API keys must exist before Phase 1 begins

---

## Phase 1 — Ingestion Pipeline
**Status:** complete

### In scope
- [x] `/ingest` endpoint accepts PDF upload — also DOCX/PPTX/HTML (FEAT-020) and re-ingest via
  `/reindex` (FEAT-024 follow-up), beyond this phase's original PDF-only scope
- [x] Parses PDF into typed elements (text, table, figure, heading) — **originally Docling
  (FEAT-004); replaced 2026-08-01 by a heuristic-based pdfplumber/python-docx/python-pptx/
  selectolax parser (FEAT-027), same contract, ~7x lower peak memory.** Wording corrected
  2026-08-07 — this line named Docling specifically, which is no longer accurate.
- [x] Text chunks embedded with Voyage — plus a Gemini `embedding-2` fallback per-batch if
  Voyage's real 3 RPM ceiling is exhausted mid-ingest (`services/embedder.py`,
  `chunks.embedding_provider` column records which)
- [x] Figures rendered to Supabase Storage as PNGs
- [x] Chunks written to `chunks` table with `document_id`, `user_id`, embedding, metadata (page number, element type, source coords)
- [x] Document row created in `documents` table with status transitions (uploaded → parsing → embedded → ready)
- [x] Multi-tenant isolation enforced via RLS from first insert
- [x] Failure handling: parse failures write to `documents.error` and set status to `failed`, no partial-ingest data left in `chunks`

### Explicitly out of scope
- Background-task durability, worker-pool/queue architecture, and rate-limiting — three facets of one decision (FastAPI `BackgroundTasks` is deliberately not a production job system for Phase 1). Consolidated under Phase 5's "Production job execution" entry below rather than scattered across phases — see that entry for the full reasoning and evidence. **Durability and rate-limiting are now closed (Phase 5); the worker-pool/queue piece remains open — see that entry.**
- ~~OCR fallback for scanned PDFs (Phase 4 — add Gemini Flash route only when Docling low-confidence pages appear in real usage)~~ — **done, FEAT-017.**
- ~~DOCX/PPTX/HTML inputs (Phase 4)~~ — **done, FEAT-020.**
- Streaming upload progress to frontend (Phase 3) — not built; the frontend polls document status post-upload (see Phase 3) rather than streaming upload-in-progress percentage
- Deduplication of identical documents (Phase 4) — not built, still genuinely out of scope

### Dependencies
- Phase 0 complete
- SCHEMA.md finalized

---

## Phase 2 — Retrieval + Generation
**Status:** complete

### In scope
- [x] `/query` endpoint accepts natural-language question + `document_id` or `document_ids[]`
- [x] Query embedded with Voyage — once per distinct embedding provider actually present in
  the requested scope, not always exactly once (see Phase 1's Gemini-fallback note)
- [x] Vector search over `chunks` (cosine similarity, RLS-filtered by `user_id`) — explicit
  `user_id`/`document_id` SQL parameters, not RLS alone (service-role client bypasses RLS;
  see STANDARDS.md)
- [x] Top-k retrieval with configurable k (default 8)
- [x] ~~Claude Sonnet~~ **Gemini 3.6 Flash** called with retrieved chunks in context, prompted
  to cite chunk IDs inline as `[1]`, `[2]`, etc. — **corrected 2026-08-07: this line never got
  updated after the Claude→Gemini switch decided on day one (commit `39f4ea7`, 2026-07-22).
  `ANTHROPIC_API_KEY` has never been used anywhere in the real code.**
- [x] Response returns answer text + array of cited chunks with source metadata (page, element type, doc name)
- [x] Basic hybrid search — BM25 via Postgres FTS layered onto vector search with RRF merge —
  generalized (FEAT-009 follow-up, 2026-07-31) to fuse N provider-partitioned vector lists (not
  just one) with FTS, once chunks could span more than one embedding provider

### Explicitly out of scope
- ~~Reranker (Phase 4)~~ — **done, FEAT-009 follow-up (2026-07-27), opt-in, default off** —
  see ARCHITECTURE.md's Locked decisions for the real quality evidence behind that default.
- ~~Conversation memory / follow-up context (Phase 3)~~ — **done, FEAT-019 (2026-07-27)**, prompt-only (no query rewriting — see ARCHITECTURE.md's Locked decisions).
- ~~Streaming responses (Phase 3)~~ — **done, FEAT-016 (2026-07-28).**
- Multi-document synthesis with attribution matrix (Phase 4) — not built, still genuinely out of scope

### Dependencies
- Phase 1 complete
- At least 3 real test documents ingested end-to-end

---

## Phase 3 — Frontend + Auth
**Status:** complete

### In scope
- [x] Supabase Auth wired: email/password + Google OAuth
- [x] Protected routes middleware in Next.js
- [x] Upload page with drag-drop, progress state, document list
- [x] Chat page with question input, streaming answer display, inline citation chips — plus
  three later modernization batches (FEAT-029/030/031: timestamps, stop/regenerate,
  conversation rename/delete) beyond this phase's original scope
- [x] Citation chip click → source panel opens showing chunk + page image if available
- [~] Document list page: filter, delete — **rename is NOT built for documents** (`PATCH
  /documents/{id}` is still listed as not-yet-defined, `API_CONTRACT.md`). Conversation
  rename/delete (a different resource) IS built (FEAT-031) — do not conflate the two when
  reading this line. Corrected 2026-08-07 from an unqualified `[ ]`, which understated how much
  of this line is actually done.
- [x] Empty states, loading skeletons, error boundaries — commit `6089845`, "Phase 3 close-out
  — error boundaries + full real E2E proof"
- [x] Mobile-responsive layout — `components/layout/sidebar.tsx`/`topbar.tsx`'s mobile menu handling

### Explicitly out of scope
- ~~User settings page beyond auth basics (Phase 4)~~ — **done, Settings batches 1-3
  (FEAT-032 through FEAT-035): profile, avatar, email change, password, sessions, preferences,
  data export, account deletion.** See those FEATURES.md entries — two are flagged there with
  real, current gaps (an uncommitted Storage migration for batch 1; batch 2 entirely
  uncommitted, including a genuine repo-integrity issue — a committed component importing a
  never-committed hook file). Not silently marked done without those caveats.
- Team/organization multi-user (Phase 4+) — not built, still genuinely out of scope
- Billing / usage limits UI (out of portfolio scope entirely) — unchanged

### Dependencies
- Phase 2 complete — `/query` returns real citations

---

## Phase 4 — Citation Verification + Polish
**Status:** complete

### In scope
- [x] Post-generation verifier pass: ~~Claude Haiku~~ **Gemini 3.5 Flash-Lite** called per
  cited claim with `(claim, cited_chunk) → supported | partial | unsupported` verdict — plus a
  fourth verdict, `unverified`, added later (FEAT-028) for when verification genuinely could
  not run. **Model name corrected 2026-08-07 — same stale Claude→Gemini gap as Phase 2's line.**
- [x] Unsupported claims stripped or flagged before returning to user — `unsupported`
  specifically is dropped and its `[N]` marker stripped from the answer text; `unverified` is
  kept with its own distinct indicator, never conflated with a real "this claim is false"
  judgment (see ARCHITECTURE.md's Verify flow)
- [x] Verifier verdict + reasoning stored in `citations` table for audit — including
  `unsupported`/`unverified` rows, which are persisted even though never returned to a live
  `/query` response (the one place they do surface: `GET /export/conversations`'s deliberately
  unfiltered export, FEAT-034)
- [x] Reranker step added to retrieval (Voyage rerank-2.5) — opt-in, default off (see Phase 2)
- [x] OCR fallback via Gemini Flash for low-confidence pages — extended to a real 3-tier chain
  (Gemini → OCR.space → Tesseract), FEAT-017
- [x] DOCX/PPTX/HTML ingestion (via Docling's native support) — FEAT-020, 2026-07-27. **Docling
  reference here is now historical** (Docling itself was removed 2026-08-01, FEAT-027; the
  DOCX/PPTX/HTML capability itself survived the parser rewrite unchanged in contract).
- [x] Conversation memory (previous Q&A in same session as context) — FEAT-019, 2026-07-27
- [x] Streaming responses via SSE — FEAT-016, 2026-07-27 (`POST /query/stream`; filed under Phase 2 in FEATURES.md, not this Phase 4 list, since it's a direct `/query` extension)
- [x] Settings/profile beyond auth basics — FEAT-032 through FEAT-035 (see Phase 3's
  out-of-scope note above for the real, current caveats on two of these). Landed here, not
  Phase 3, matching Phase 3's own original deferral.

**Reconciled 2026-08-07:** every item above except the two newly-added lines (verifier storage
detail, Settings) was already functionally complete but left unchecked/mis-worded since before
FEAT-020 shipped — a prior session's own note (left below for the historical record) had
already spotted this and explicitly deferred fixing it; this pass closes it out for real rather
than deferring it a second time.

### Explicitly out of scope
- Strategy selector UI (v2 — after v1 shipped)
- Fine-tuning any model (never in scope for this project)

### Dependencies
- Phase 3 complete + real usage feedback

---

## Phase 5 — Deploy + Portfolio Polish
**Status:** in-progress

### In scope
- [ ] Vercel prod deploy of `apps/web` — not yet done (see Phase 0's note on Vercel project
  linking, also unconfirmed)
- [x] Render prod deploy of `apps/api` with env vars + health check — 2026-07-27. Live at `https://docify-api.onrender.com`, real health check and real authenticated request both verified (CHANGELOG.md). **Directly confirms the "Production job execution" risk below, not just predicts it:** a real `/ingest` call OOM-crashed the free-tier instance mid-Docling-parse — the crashed background task left its document silently stuck in `parsing` forever, exactly the "a process restart silently loses in-flight work" failure mode this entry already named before any real deploy existed to prove it. **Root cause since fixed** (FEAT-027 replaced Docling, ~7x lower peak parse memory) — not fixed *in this specific Phase 5 pass*, but no longer an open risk as of 2026-08-01.
- [ ] Custom domain (if user has one)
- [ ] Landing page with demo video / gif and "try with sample doc" flow
- [ ] README.md at repo root: architecture diagram, tech decisions, how to run locally — a
  README exists (29 lines, 2026-07-22) but is a quick-start stub, not this checklist's full
  scope (no architecture diagram, no tech-decisions writeup) — left unchecked rather than
  credited for partial coverage
- [ ] **Production job execution for `/ingest` (and `/query` once built)** — FastAPI `BackgroundTasks` is not a production job system: no queue, no worker pool, no timeout/backpressure, no per-user concurrency cap, and a hung or crashed task is invisible beyond ordinary log output (a process restart silently loses in-flight work). Three facets of the same underlying decision, tracked together here rather than as separate scattered bullets across phases:
  - [x] **Durability — closed 2026-08-02 via a lazy/reactive approach, not a real worker queue (FEAT-024 follow-up).** `GET /documents` (`routes/documents.py`) opportunistically reaps any of the requesting user's own documents still stuck in `'parsing'`/`'embedded'` past a 30-minute threshold to `'failed'`, and `POST /reindex/{document_id}` (`routes/ingest.py`) lets the user recover it (or any document, for other reasons — a Voyage-fallback re-embed, a retry of FEAT-017's OCR chain) by re-running the pipeline from the file already in Storage. **Explicit statement of the tradeoff this represents, not left implicit:** this is NOT a real crash-recovery system — there is no automatic retry, no dead-letter queue, no proactive detection (a document stays visibly stuck until a user's own `GET /documents` call happens to run past the threshold), and the 30-minute threshold is a documented, not proven-optimal, judgment call (`STUCK_DOCUMENT_THRESHOLD_SECONDS`, `routes/ingest.py`). It is deliberately the SMALLEST fix that closes the real, proven gap (a document stuck forever with **zero** path to recovery) without building the worker-pool/queue infrastructure below. **Why this tradeoff is acceptable at this project's scale:** a solo-dev portfolio project running one Render free-tier instance has no real concurrent-user load to make "a user has to reload their document list to trigger recovery" a meaningful UX cost, and the actual failure this closes (the FEAT-027-era Render OOM crash) is now independently far less likely to recur (parser rewrite: ~160MB peak vs. the old 1144.6MB) — this is a durability *backstop* for a rare event, not a load-bearing system for common ones. Revisit with a real queue/worker system (the entry below) if either changes: real concurrent traffic, or a failure mode this reactive approach doesn't cover (e.g. a task hung, not crashed — still consuming resources indefinitely with no timeout of its own; not addressed here).
    Real regression test, not a hand-crafted row: `apps/api/tests/test_reindex.py`'s `test_reaper_and_reindex_recover_a_document_whose_pipeline_was_really_killed` interrupts a real call to `run_ingest_pipeline()` mid-flight via a `BaseException` (bypassing the function's own `except Exception` cleanup entirely, the same way a real SIGKILL/OOM-kill does), confirms the document is genuinely stuck at `'parsing'` with no error message (proving the interruption, not a handled failure, actually happened), then drives the real `GET /documents` and `POST /reindex/{id}` routes end-to-end to confirm full recovery to `'ready'`.
  - **Worker-pool/queue architecture — still open, not addressed by durability's lazy-reaper fix above.** Heavy Docling/Voyage work still runs on the request-serving process; no timeout/backpressure/per-user concurrency cap exists for a task that's genuinely hung (not crashed) rather than dead. **Still genuinely open as of 2026-08-07** — nothing in the settings/chat-modernization work since touched this.
  - [x] **Rate-limiting** on `/ingest`, `/reindex`, and `/query`, to protect free-tier quotas from concurrent or abusive load — done 2026-07-28, FEAT-024 (`apps/api/rate_limit.py`, `routes/ingest.py`/`routes/query.py`); extended 2026-08-02 (FEAT-024 follow-up). `slowapi`, in-memory storage (no `storage_uri=`) for per-minute limits — a deliberate, stated tradeoff, not an unexamined default: this project runs a single Render instance with no Redis anywhere else in the stack, and adding one just for this would be real new infrastructure for a problem that doesn't need it yet. **Known, accepted limitation, logged here explicitly:** per-minute limits reset on every redeploy/cold-start restart (acceptable — a 15-minute idle gap already exceeds any per-minute window, so a restart-induced reset and a legitimate window rollover are indistinguishable in practice), and this in-memory approach stops being correct the moment this service ever runs as more than one instance, since each instance would track its own separate counters, silently multiplying the effective limit by instance count. Revisit with a shared store (Redis via `storage_uri=`) if either changes. Keyed per-user (`request.state.user_id`, JWT-verified — never IP) for the per-user limits, values derived from real measured vendor ceilings (Voyage's real 3 RPM, Gemini's real 20/day for `gemini-2.5-flash` specifically — `.agent/MEMORY.md`), not round numbers — see `API_CONTRACT.md`'s `/ingest`/`/reindex`/`/query` entries for the exact numbers and reasoning. **`GET /export/conversations` and `DELETE /account` (FEAT-034/035) are deliberately NOT rate-limited** — same real-cost profile as `/documents`/`/conversations` (read/delete-only against already-stored rows, zero Voyage/Gemini calls), confirmed by extending this feature's own not-rate-limited regression test rather than asserting it by absence of a decorator.
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
- ~~Building a custom parser to replace Docling~~ — **stale wording, not a stale decision.**
  FEAT-027 replaced Docling with a heuristic-based custom parser (pdfplumber/python-docx/
  python-pptx/selectolax) — meaning the literal action this line forbade is exactly what
  happened, for a real, load-bearing production reason (Docling's memory cost OOM-crashed the
  free-tier deploy), not a scope violation. Corrected 2026-08-07 to describe the real locked
  boundary: no *fine-tuned/ML-trained* custom parser — the heuristic rewrite doesn't train
  anything, so it never actually crossed this line.
- Real-time collaboration features
- Mobile native app
- Public API / developer platform
- Billing / payments / subscriptions
- Anything requiring paid infra beyond free-tier expiry
