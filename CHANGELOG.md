# Changelog

Append-only record of every meaningful change. Never edit past entries — add a correction entry instead.

Entry types: `feature` · `fix` · `decision` · `refactor` · `test` · `infra` · `scope-change`

---

## 2026-07-22 — feature: GET /health endpoint (FEAT-002)
**Phase:** 1 (Ingestion Pipeline)
**Feature:** FEAT-002
**Decision:** Implemented `/health` exactly per API_CONTRACT.md's spec — `{status, version, timestamp}` on 200, no auth. `apps/api/models/health.py` holds the `HealthResponse` Pydantic model (kept separate from the route per STANDARDS.md's models/ convention even though it's a one-liner, for consistency with how every later endpoint will be structured); `apps/api/routes/health.py` holds the route itself. `version` is a hardcoded `"0.1.0"` constant matching `pyproject.toml` rather than read via `importlib.metadata` — simpler and doesn't depend on the package being installed with matching distribution metadata. `timestamp` is formatted as `%Y-%m-%dT%H:%M:%SZ` to match the API_CONTRACT.md example literally (`Z` suffix, not `+00:00`). Wired `health.router` into `main.py` — not in FEAT-002's original Files list but required for the endpoint to be reachable at all; `main.py` had no routes registered since FEAT-000. Added `[tool.pytest.ini_options] pythonpath = ["."]` to `apps/api/pyproject.toml` — without it, pytest couldn't resolve `from main import app` in the test (pytest only added `tests/` to `sys.path`, not `apps/api/` itself). Deliberately did not add any auth check — FEAT-003 owns JWT middleware, and this endpoint must stay open for uptime monitors per its own spec.
**Changed:** `apps/api/routes/health.py` (new), `apps/api/models/health.py` (new), `apps/api/tests/test_health.py` (new), `apps/api/main.py` (router wired), `apps/api/pyproject.toml` (pytest pythonpath config), `.agent/FEATURES.md` (FEAT-002 → complete, acceptance criteria checked, Files list corrected).
**Impact:** First live endpoint in the API. Verified locally: `curl http://localhost:8000/health` → `{"status":"ok","version":"0.1.0","timestamp":"..."}`, 200, no auth header needed. `uv run pytest` — 2 passed (health), 14 skipped (migrations, no local DB configured — expected). FEAT-003 (JWT middleware) can now build on a running app with at least one route to protect *around*, not through.
**Rollback:** Remove the four new files, revert `main.py`'s router registration and `pyproject.toml`'s pytest config, revert `.agent/FEATURES.md`.

---

## 2026-07-22 — feature: Supabase initial migration applied + verified (FEAT-001)
**Phase:** 0 (Setup)
**Feature:** FEAT-001
**Decision:** Wrote the initial schema migration (`apps/api/migrations/20260722_001_initial.sql`) transcribing everything in `.agent/SCHEMA.md` verbatim: `vector`/`pgcrypto` extensions, 4 enums, all 5 tables (`documents`, `chunks`, `conversations`, `messages`, `citations`) with their indexes — including the HNSW index on `chunks.embedding` — full RLS policy set, and the `uploads`/`figures` storage buckets with their storage.objects policies. Statements are idempotent (`if not exists`, `drop policy if exists`) so it's safe to re-run. Also wrote a companion dashboard-runnable verification script (`verify_20260722_001.sql`) that returns one `OK`/`MISSING` row per table/RLS/policy/extension/index/bucket check, and `apps/api/db/client.py` (service-role client) plus `apps/api/tests/test_migrations.py` (pytest equivalent of the same checks via direct Postgres introspection, for when a `TEST_DATABASE_URL` is available — not used this round). Rather than local Docker Supabase (the FEATURES.md-default verification path), owner chose to apply directly to the already-provisioned live Supabase project (`nbrfjbjjjhawscncshdz`) via the dashboard SQL editor — Docker Desktop was installed but not running, and a real project already existed from earlier credential setup, so local-first verification would have just added a redundant step. Owner ran both scripts manually and confirmed every check in `verify_20260722_001.sql` returned `OK`. Files were briefly renamed off-convention (`migration1script.sql` / `verifymigration1.sql`) then restored to the `YYYYMMDD_NNN_description.sql` convention after confirming it was safe: linked the project with `supabase link` and ran `supabase migration list`, which returned `"migrations":[]` — dashboard-applied SQL never writes to Supabase's CLI-tracked `supabase_migrations.schema_migrations` table, so there was no CLI-side history to desync. `.agent/SCHEMA.md §Migration log` updated to record this (including the empty `migration list` result, so it's clear that's expected and not a gap).
**Changed:** `apps/api/migrations/20260722_001_initial.sql` (new), `apps/api/migrations/verify_20260722_001.sql` (new), `apps/api/db/client.py` (new), `apps/api/tests/test_migrations.py` (new), `apps/api/pyproject.toml` + `apps/api/uv.lock` (added `psycopg[binary]` dev dep for the introspection tests), `.agent/FEATURES.md` (FEAT-001 → complete, acceptance criteria checked), `.agent/SCHEMA.md` (§Migration log entry added).
**Impact:** The live Supabase project now has the full v1 schema, RLS, and storage buckets. FEAT-002 through FEAT-012 (everything touching `documents`, `chunks`, `conversations`, `messages`, `citations`, or the storage buckets) can now be built against a real schema instead of a hypothetical one.
**Rollback:** Run the `ROLLBACK` block at the bottom of `apps/api/migrations/20260722_001_initial.sql` against the live project via the dashboard SQL editor (drops policies, storage buckets, tables, then enum types, in dependency order). Revert `.agent/FEATURES.md` and `.agent/SCHEMA.md` to the prior commit.

---

## 2026-07-22 — feature: repo skeleton scaffolded (FEAT-000)
**Phase:** 0 (Setup)
**Feature:** FEAT-000
**Decision:** Scaffolded `apps/web` (Next.js 14.2 App Router + TypeScript + Tailwind, via `pnpm create next-app`) and `apps/api` (FastAPI on Python 3.12 via `uv init`, deps: fastapi, uvicorn, docling, voyageai, google-genai, supabase, pydantic, python-dotenv; dev: pytest, pytest-asyncio, httpx). Restructured both to match STANDARDS.md's directory layout — `apps/web/app/(auth)`, `app/(app)`, `app/api`, `components/{ui,layout}`, `lib/{supabase,api,types}`; `apps/api/{routes,services,db,models,migrations,tests}` — with `.gitkeep` placeholders for empty dirs. `main.py` is a bare `FastAPI()` instance with no routes (routes start at FEAT-002). `middleware.ts` deliberately not created — an empty one breaks Next.js, and real auth logic belongs to FEAT-013. `uv` wasn't installed on this machine; installed it via `pip install --user uv`, which then fetched Python 3.12 automatically (system had only 3.14) — no system Python install needed. Wrote root `README.md` (quick-start for both apps) and expanded root `.gitignore` (node_modules, .next, __pycache__, .venv, .env*, editor/OS cruft — `uv.lock` and `pnpm-lock.yaml` intentionally still tracked per STANDARDS.md). Added per-app `.env.example` reflecting the current (Gemini, not Anthropic) provider decision. Verified all four FEAT-000 acceptance criteria directly: `uv sync` succeeded, `pnpm install` succeeded (implicit in scaffold), `pnpm dev` served 200 on :3000, `uv run uvicorn main:app` served 200 (`/openapi.json`) on :8000. Also fixed a `/gap-check` bug found while verifying: Check 1 flagged every `Files:` entry across the whole registry as a critical missing-file gap regardless of feature status, so any `planned` feature's not-yet-created files were reported as gaps against FEAT-000. Patched it to skip `status: planned` features and to accept an optional `FEAT-NNN` arg to scope the check.
**Changed:** `apps/web/**` (new), `apps/api/**` (new), `README.md` (new), `.gitignore`, `.agent/FEATURES.md` (FEAT-000 → complete, fixed ambiguous `.env.example` file listing), `.agent/scripts/gap-check.sh` (scoping + planned-status fix).
**Impact:** FEAT-001 (Supabase project + migration) can now start — it depends on `apps/api/db/` existing, which it now does. `/gap-check` is now meaningfully scopable per-feature instead of always dumping the full backlog.
**Rollback:** `rm -rf apps/ README.md`, revert `.gitignore` and `.agent/FEATURES.md` to prior commit, revert `.agent/scripts/gap-check.sh`. `uv` (installed via pip) can be removed with `pip uninstall uv` if desired — nothing else depends on it being globally present, since `uv` manages its own Python versions per-project.

---

## 2026-07-22 — decision: generation + verification switched from Claude to Gemini
**Phase:** 0 (Setup)
**Feature:** —
**Decision:** Reversed the generation and citation-verification provider decision from Claude to Gemini, consolidating all LLM calls onto a single provider. Ran `/api-check gemini` and verified current model IDs live: `gemini-3.6-flash` (generation, replaces Claude Sonnet) and `gemini-3.5-flash-lite` (verification/LLM-as-judge, replaces Claude Haiku), cached in `.agent/api-docs/gemini.md`. Gemini was already in the stack for OCR fallback (`gemini-2.5-flash`, left unchanged), so this removes the Anthropic SDK and second API credential rather than running two providers. Moved the "post-credit-expiry generation provider" entry in `.agent/MEMORY.md §Open questions` to `§Decision log`. Voyage (embeddings) is untouched — separate decision, separate provider.
**Changed:** `AGENT.md` (stack line, locked/open decisions), `.agent/ARCHITECTURE.md` (system diagram, stack table, query/verify flows, module map, locked/open decisions), `.agent/API_CONTRACT.md` (error codes, response metadata example, error list), `.agent/FEATURES.md` (FEAT-010 generator wrapper, FEAT-011 verifier acceptance criteria, pyproject.toml deps note), `.agent/MEMORY.md` (open question → decision log), `.agent/api-docs/gemini.md` (new).
**Impact:** FEAT-010 and FEAT-011 now target the Gemini SDK instead of Anthropic's. No code exists yet for either (both `planned`), so this is a pure documentation change with no migration required.
**Rollback:** Revert this commit, or find-replace `gemini-3.6-flash` → `claude-sonnet-latest` and `gemini-3.5-flash-lite` → `claude-haiku-latest` across the changed files and move the MEMORY.md entry back to `§Open questions`.

---

## 2026-07-22 — decision: project renamed to Docify
**Phase:** 0 (Setup)
**Feature:** —
**Decision:** Locked final project name as Docify, closing the "final project name" entry in `.agent/MEMORY.md §Open questions` (moved to `§Decision log`). Find-replaced the `multimodal-rag` placeholder to `docify` in AGENT.md's `name:` field and `.agent/API_CONTRACT.md`'s placeholder production URL (`docify-api.onrender.com`). Moved the corresponding AGENT.md open-decision item to Locked decisions. No occurrences of the placeholder existed in `.agent/ARCHITECTURE.md` or `.agent/FEATURES.md` — verified via grep, nothing to change there. (Unrelated: "multimodal-RAG" as a technique name in MEMORY.md and this changelog's own Setup entry refers to the RAG strategy category, not the project name, and was left as-is.)
**Changed:** `AGENT.md`, `.agent/API_CONTRACT.md`, `.agent/MEMORY.md`.
**Impact:** All project-identity references now resolve to the real name. Unblocks FEAT-023 landing page work, which was waiting on this.
**Rollback:** Revert this commit, or find-replace `docify` back to `multimodal-rag` in the three changed files and move the MEMORY.md entry back to `§Open questions`.

---

## 2026-07-22 — infra: agent-os subsystem install + ground-truth doc repair
**Phase:** 0 (Setup)
**Feature:** —
**Decision:** Ran full agent-os bootstrap in update mode. Found SCOPE.md, FEATURES.md, GAPS.md, MEMORY.md, and STANDARDS.md had been written to `.agent/api-docs/` (reserved for external API doc caches) instead of `.agent/`, silently breaking every pointer in AGENT.md §PROJECT. Moved all five to their correct location before bootstrapping so the generic templates didn't overwrite them with placeholders. Also fixed three latent bugs in the generated scripts: `feature-check.sh`, `ralph-loop.sh`, and `ralph-status.sh` anchored on `## [FEAT-` (h2) while this project's FEATURES.md uses `### [FEAT-` (h3) — all three now accept either. `feature-check.sh` also searched for the literal substring `Status: planned` when the actual format is `**Status:** planned`, undercounting every status bucket. Initialized a local git repo (no commits yet) since the session-end protocol assumes one and SCOPE.md Phase 0 already listed it as a pending item. Added `.claude/commands/ctx-scope.md` and `ctx-load.md`, which AGENT.md's command grid referenced but bootstrap never wired up.
**Changed:** `.agent/{SCOPE,FEATURES,GAPS,MEMORY,STANDARDS}.md` (moved), `.agent/scripts/*.sh` (11 scripts installed, 3 patched), `.claude/commands/*.md` (13 commands, 2 added), `.agent/index.json` (built, 0 symbols — no source yet), `.gitignore` (created), local git repo (initialized).
**Impact:** `/gap-check`, `/feature-check`, and `/ralph-loop` now correctly parse this project's actual FEATURES.md instead of silently reporting zero features. All AGENT.md pointers now resolve to real files.
**Rollback:** `rm -rf .agent/scripts .agent/index.json .claude/commands .git .gitignore` and move the five docs back to `.agent/api-docs/` (not recommended — that reintroduces the original bug).

---

## 2026-07-22 — decision: project inception + stack lock
**Phase:** 0 (Setup)
**Feature:** —
**Decision:** Locked the v1 stack after multi-turn design session. Chose layout-aware structured parsing (Docling) over the other three multimodal-RAG strategies (extract-to-text, unified multimodal embeddings, page-as-image) as the single v1 approach — widest applicable, best portfolio depth, fully free at portfolio scale. Voyage multimodal-3.5 for embeddings (unified encoder avoids CLIP's modality gap, 200M tokens + 150B pixels free). Supabase for Postgres + pgvector + Auth + Storage. FastAPI on Render for the Python backend (Vercel serverless can't fit Docling under 250MB). Next.js 14 on Vercel for frontend. Multi-tenant from schema layer via Postgres RLS from day one — no retrofit later. Claude for generation and citation verification while $120 promotional API credits are available (expire Aug 9 + Sep 19); Gemini Flash reserved for OCR fallback only.
**Changed:** Repo initialized. AGENT.md, CHANGELOG.md, and all .agent/ ground-truth docs drafted.
**Impact:** Downstream — all future feature work references these decisions. Post-Sep-19, generation provider becomes an open decision again.
**Rollback:** Discard repo and restart planning.

## 2026-07-22 — decision: four-agent development workflow
**Phase:** 0 (Setup)
**Feature:** —
**Decision:** Four-agent split locked: claude-code (backend + architecture), claude-design (frontend UI), gemini (integration glue + auth flows), codex (bug hunting + pre-merge review). Each agent owns a lane; cross-lane work goes through HANDOFF.md `## Agent Suggestions` inbox, not silent expansion. Commit messages must end with agent tag `[claude-code|claude-design|gemini|codex]` for later attribution and debugging. Solo-dev alternative (two-agent collapse) was discussed and rejected — user prefers full multi-agent experience for the portfolio angle.
**Changed:** AGENT.md §AGENT ROLES section added. RULE 6 and RULE 7 codified.
**Impact:** Every agent session reads AGENT.md and stays in lane. Handoff discipline becomes the load-bearing structure.
**Rollback:** Collapse to single or two-agent workflow — remove role table from AGENT.md, drop agent-tag rule.
