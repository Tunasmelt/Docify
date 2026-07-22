# Changelog

Append-only record of every meaningful change. Never edit past entries — add a correction entry instead.

Entry types: `feature` · `fix` · `decision` · `refactor` · `test` · `infra` · `scope-change`

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
