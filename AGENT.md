# ⚡ COMMAND GRID — read this first, every session

| Command          | What it does                                    | Run when                                      |
|------------------|-------------------------------------------------|-----------------------------------------------|
| /ctx-audit       | Context health check                            | Session start — mandatory (run /ctx-map first on a fresh clone) |
| /ctx-load        | Read HANDOFF.md and resume                      | Starting any session mid-task                 |
| /ctx-search      | Semantic symbol/function search via index       | Need to find where something lives            |
| /ctx-map         | Rebuild .agent/index.json symbol index          | Files added/deleted, index stale              |
| /ctx-scope       | Restrict file reads to specified paths          | Focused sub-task, avoid context bleed         |
| /api-check       | Verify live API docs before integration         | Before writing/editing any external API call  |
| /gap-check       | Detect implementation gaps                      | Before marking any feature complete           |
| /feature-check   | Verify feature registry vs actual code          | Phase completion, before release              |
| /test-scaffold   | Generate test stubs for a feature               | After writing acceptance criteria             |
| /changelog       | Write structured changelog entry                | After any meaningful change                   |
| /ctx-dump        | Write session state → HANDOFF.md                | Before /clear, end of session, context >50%   |
| /memory-sync     | Sync session decisions → MEMORY.md              | End of session, after ctx-dump                |
| /ralph-loop      | Autonomous build loop over FEATURES.md          | Run features unattended with real verifier    |

> **RULE 1:** Never read a file to find a symbol — run /ctx-search first. `.agent/index.json` is gitignored, so on a fresh clone run /ctx-map once to build it.
> **RULE 2:** Read HANDOFF.md + MEMORY.md §Anti-patterns before starting any work.
> **RULE 3:** Never let context exceed 60% before running /ctx-dump.
> **RULE 4:** Never mark a feature complete without passing tests and /gap-check.
> **RULE 5:** Before writing code against any external API, run /api-check for that API.
> **RULE 6:** Stay inside the current task's scope — out-of-scope ideas go to HANDOFF.md `## Agent Suggestions`, not silent expansion.
> **RULE 7:** Every commit message ends with the agent tag of the committing agent (in practice `[claude-code]`).

---

# §PROJECT

```yaml
name:        docify
type:        saas
agent-index: .agent/index.json
handoff:     HANDOFF.md
memory:      .agent/MEMORY.md
changelog:   CHANGELOG.md
scope:       .agent/SCOPE.md
features:    .agent/FEATURES.md
schema:      .agent/SCHEMA.md
api-contract:.agent/API_CONTRACT.md
```

## Stack
- Next.js 14 App Router + TypeScript + Tailwind + shadcn/ui (frontend, Vercel)
- FastAPI + Python 3.12 (backend, Render via Docker)
- pdfplumber / python-docx / python-pptx / selectolax (layout-aware parsing, self-hosted — replaced Docling in FEAT-027)
- OCR fallback for scanned PDF pages: Gemini 2.5 Flash → OCR.space → Tesseract
- Voyage multimodal-3.5 (embeddings) with Gemini embedding-2 fallback; Voyage rerank-2.5 (opt-in)
- Gemini 3.6 Flash / 3.5 Flash-Lite (generation + citation verification)
- Supabase — Postgres + pgvector + Auth + Storage

## Architecture pointers
Read only the file relevant to your current task.

| Area                | Read this file                         |
|---------------------|----------------------------------------|
| Architecture        | .agent/ARCHITECTURE.md                 |
| Database schema     | .agent/SCHEMA.md                       |
| Internal API        | .agent/API_CONTRACT.md                 |
| External APIs       | .agent/api-docs/{voyage,gemini,supabase,supabase-storage-py,ocrspace,parser-libs}.md |
| Coding standards    | .agent/STANDARDS.md                    |
| Feature registry    | .agent/FEATURES.md                     |
| Scope + phases      | .agent/SCOPE.md                        |
| Agent memory        | .agent/MEMORY.md                       |
| Local dev setup     | docs/DEVELOPMENT.md                    |
| Deploying           | docs/DEPLOYMENT.md                     |

## Open decisions
- [ ] Strategy selector as v2 feature or later

## Locked decisions
- [x] Project name: Docify
- [x] Layout-aware structured parsing as v1 strategy (pdfplumber-based parser, FEAT-027; originally Docling)
- [x] Multi-tenant from day one — RLS for direct client access, explicit `user_id` scoping on every service-role query
- [x] Monorepo — apps/web + apps/api
- [x] Deploy Vercel (web) + Render (api)
- [x] Voyage multimodal-3.5 for embeddings (unified encoder, generous free tier)
- [x] Gemini (3.6 Flash generation / 3.5 Flash-Lite verification) for generation + verification — consolidated onto one provider

---

# §AGENT ROLES

One implementing agent does all work; other agents are used only for one-off reviews.

| Agent       | Role                                                                                     |
|-------------|------------------------------------------------------------------------------------------|
| claude-code | All implementation: API, schema, ingestion, retrieval, verification, frontend, docs      |
| codex (or any reviewer) | Independent review passes — findings go in `.agent/reviews/YYYY-MM-DD*.md` and are fixed by claude-code |

The original four-agent lane model (claude-code / claude-design / gemini / codex) was retired; see MEMORY.md §Decision log 2026-10-06. Design references (e.g. Claude Design mockups) are inputs, not a separate lane.

---

# §RULES

## Structural (enforced by hooks + scripts)
- Never commit directly to `master` (the default branch)
- Never read more than 3 files without /ctx-search first (build the index with /ctx-map if it's missing)
- Never mark a feature complete without: tests passing + /gap-check clean + CHANGELOG entry
- Never write code against an external API without a fresh .agent/api-docs/<api>.md entry — run /api-check first
- Never change ARCHITECTURE.md locked decisions or SCHEMA.md without human confirmation
- Every service-role query in FastAPI must be scoped by the JWT-derived `user_id` — the service role bypasses RLS, so this filter is the tenant boundary

## Behavioural
- Output diffs not full files for targeted edits
- Batch all questions into one message
- Skip recaps: "No recap. Proceed."
- Do not auto-install packages — list and ask
- Every decision made this session → CHANGELOG entry before session ends
- Every commit message ends with agent tag in brackets

---

# §SESSION

## Starting a session
```
1. Run /ctx-audit — address all warnings before proceeding
2. Read HANDOFF.md in full (if exists)
3. Read .agent/MEMORY.md §Anti-patterns and §Open-questions
4. State session goal in one sentence
5. Run /ctx-scope [relevant directories]
```

## Ending a session
```
1. Run /gap-check — document any new gaps in .agent/GAPS.md
2. Run /ctx-dump — write HANDOFF.md
3. Run /memory-sync — extract decisions → MEMORY.md
4. git add .agent/ CHANGELOG.md        # HANDOFF.md is gitignored — it stays local
5. git commit -m "<type>(<scope>): <summary> [<agent-tag>]"
```

## Task brief format (use this every time)
```
Context: [2–3 sentences on project state]
Goal:    [one sentence — this session accomplishes X]
Files:   [explicit list]
Avoid:   [files/areas not to touch]
Phase:   [current phase from SCOPE.md]
Feature: [FEAT-XXX if applicable]
Task:    [specific instruction]
```
