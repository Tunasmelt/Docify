# ⚡ COMMAND GRID — read this first, every session

| Command          | What it does                                    | Run when                                      |
|------------------|-------------------------------------------------|-----------------------------------------------|
| /ctx-audit       | Context health check                            | Session start — mandatory                     |
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

> **RULE 1:** Never read a file to find a symbol — run /ctx-search first.
> **RULE 2:** Read HANDOFF.md + MEMORY.md §Anti-patterns before starting any work.
> **RULE 3:** Never let context exceed 60% before running /ctx-dump.
> **RULE 4:** Never mark a feature complete without passing tests and /gap-check.
> **RULE 5:** Before writing code against any external API, run /api-check for that API.
> **RULE 6:** Never touch a file outside your agent's lane — write a HANDOFF suggestion instead.
> **RULE 7:** Every commit message ends with the agent tag: `[claude-code]`, `[claude-design]`, `[gemini]`, `[codex]`.

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
- Next.js 14 App Router + TypeScript + Tailwind + shadcn/ui (frontend)
- FastAPI + Python 3.12 (backend)
- Docling (layout-aware parsing, self-hosted)
- Voyage multimodal-3.5 (embeddings)
- Claude Sonnet / Haiku (generation + citation verification)
- Gemini Flash (OCR fallback only)
- Supabase — Postgres + pgvector + Auth + Storage

## Architecture pointers
Read only the file relevant to your current task.

| Area                | Read this file                         |
|---------------------|----------------------------------------|
| Architecture        | .agent/ARCHITECTURE.md                 |
| Database schema     | .agent/SCHEMA.md                       |
| Internal API        | .agent/API_CONTRACT.md                 |
| External APIs       | .agent/api-docs/{voyage,claude,...}.md |
| Coding standards    | .agent/STANDARDS.md                    |
| Feature registry    | .agent/FEATURES.md                     |
| Scope + phases      | .agent/SCOPE.md                        |
| Agent memory        | .agent/MEMORY.md                       |

## Open decisions
- [ ] Strategy selector as v2 feature or later
- [ ] Post-credit-expiry generation provider (Claude paid vs Gemini swap)

## Locked decisions
- [x] Project name: Docify
- [x] Layout-aware structured parsing as v1 strategy (Docling)
- [x] Multi-tenant from day one — RLS at schema level
- [x] Monorepo — apps/web + apps/api
- [x] Deploy Vercel (web) + Render (api)
- [x] Voyage multimodal-3.5 for embeddings (unified encoder, generous free tier)
- [x] Claude for generation + verification while credits last

---

# §AGENT ROLES

| Agent          | Owns                                                                    | Never touches                                    |
|----------------|-------------------------------------------------------------------------|--------------------------------------------------|
| claude-code    | API design, schema, ingestion, retrieval, verification prompts, ARCH/SCHEMA/API_CONTRACT | Frontend visuals                                |
| claude-design  | Frontend UI, components, styling, page layouts                          | Business logic, DB queries                       |
| gemini         | Frontend↔backend wiring, auth flows, user CRUD, integration glue        | API contract changes, schema changes             |
| codex          | Bug hunting, test coverage audits, refactor suggestions, pre-merge review| Feature implementation outside bug-fix scope    |

Cross-lane work → write suggestion to HANDOFF.md `## Agent Suggestions` section, not silent expansion.

---

# §RULES

## Structural (enforced by hooks + scripts)
- Never commit directly to `main`
- Never read more than 3 files without /ctx-search first
- Never mark a feature complete without: tests passing + /gap-check clean + CHANGELOG entry
- Never write code against an external API without a fresh .agent/api-docs/<api>.md entry — run /api-check first
- Never change ARCHITECTURE.md locked decisions or SCHEMA.md without human confirmation
- Never bypass RLS in queries (multi-tenant guarantee)

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
4. git add .agent/ HANDOFF.md CHANGELOG.md
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
