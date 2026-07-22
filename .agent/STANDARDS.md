# Standards

Coding standards and conventions for this project. Violations are flagged in GAPS.md by `/gap-check`.

---

## Naming

### Files & folders
- Frontend: `kebab-case.tsx` for files, `PascalCase` for component names within them
  - `components/chat/message-bubble.tsx` exports `MessageBubble`
- Backend Python: `snake_case.py` for files, `snake_case` for functions, `PascalCase` for classes
  - `services/embedder.py` exports `class VoyageEmbedder:` and `def embed_chunks(...)`
- Migrations: `YYYYMMDD_NNN_short_description.sql` — e.g. `20260722_001_initial.sql`

### Symbols
- **Constants:** `SCREAMING_SNAKE_CASE` in both TS and Python
- **Types (TS):** `PascalCase` — `type Citation = {...}`, `interface DocumentRow {}`
- **React components:** `PascalCase`, one component per file (except tiny helpers)
- **Hooks:** `useSomething` prefix
- **Python classes:** `PascalCase`, one class per file for services
- **Test files:**
  - Frontend: `foo.test.ts` for unit, `foo.spec.ts` for integration/e2e (Playwright)
  - Backend: `test_foo.py` for pytest

### Database
- **Tables:** plural `snake_case` — `documents`, `chunks`, `citations`
- **Columns:** `snake_case` — `user_id`, `created_at`, `page_number`
- **Enums:** singular `snake_case` — `document_status`, `element_type`
- **Indexes:** `{table}_{column(s)}_idx` — `chunks_user_idx`, `documents_status_idx`
- **RLS policies:** `{table}_{operation}` — `documents_select`, `chunks_insert`

### Env vars
- `SCREAMING_SNAKE_CASE`
- Grouped by service prefix: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
- Every app has `.env.example` at its root listing all required vars

---

## Structure

### Frontend directory layout
```
apps/web/
├── app/                       Next.js App Router pages
│   ├── (auth)/                unauthenticated pages
│   ├── (app)/                 authenticated pages (protected by middleware)
│   └── api/                   thin proxy route handlers
├── components/
│   ├── ui/                    shadcn primitives — never modify these directly
│   ├── {feature}/             feature-scoped components
│   └── layout/                nav, sidebar, shell
├── lib/
│   ├── supabase/              client wrappers
│   ├── api/                   FastAPI client
│   └── types/                 shared TS types
└── middleware.ts              auth gate for (app)/*
```

### Backend directory layout
```
apps/api/
├── main.py                    FastAPI app + startup + middleware
├── routes/                    one file per resource
├── services/                  one class per concern (parser, embedder, etc.)
├── db/                        Supabase client + typed queries
├── models/                    Pydantic request/response models
├── migrations/                timestamped SQL files
└── tests/                     mirrors services/ + routes/
```

### Rules
- **One default export per file** (frontend) — no barrel files (`index.ts` re-exports) except in `components/ui/`
- **No cross-feature imports** — `components/chat/*` cannot import from `components/documents/*`. Shared bits move to `lib/` or `components/ui/`
- **Services take dependencies via constructor** (backend) — makes testing possible without monkey-patching

---

## Error handling

### Frontend
- **Every fetch to FastAPI goes through `lib/api/client.ts`** — no raw `fetch()` in components
- The client throws typed `ApiError { code, message, status }` on non-2xx responses
- Every protected route has an `error.tsx` boundary
- User-visible errors surface via a toast (shadcn) — technical details logged, human message displayed
- No `throw new Error("...")` with string-only messages — always subclass or use typed enum codes

### Backend
- **Every route wraps errors in the standard envelope** (see API_CONTRACT.md)
- Use FastAPI's exception handlers, not per-route try/except
- Custom exceptions live in `apps/api/errors.py` with codes matching the API contract
- Never leak stack traces or `str(exception)` to the client
- Log full traceback server-side with request context (user_id, endpoint, request_id)

### Never do
- `console.log` in committed frontend code — use `console.error` for debug traces
- `print()` in committed backend code — use `logger.info/warn/error`
- Bare `except:` in Python — always name the exception type
- Swallowed promises in TS — always `await` or explicitly `.catch()`

---

## Logging

Structured logs everywhere. Format:
```
[LEVEL] [ISO8601 timestamp] [component] message {key: value, key: value}
```

Levels: `DEBUG` · `INFO` · `WARN` · `ERROR` · `FATAL`

### What must be logged
- Every route entry: method, path, user_id, request_id
- Every downstream API call: service, latency_ms, status
- Every parse/embed/generate failure: full error + input identifiers (not content)
- Every RLS violation attempt: user_id, resource, attempted action

### What must never be logged
- API keys or JWTs
- Full document content or user question text (log identifiers instead)
- Request bodies containing PII
- Storage-path values that include user_ids (log document_id instead)

---

## Testing

Every feature has tests at up to three levels:

| Level | Tool | What it tests | Where |
|---|---|---|---|
| Unit | vitest / pytest | Pure functions, no I/O | `*.test.ts`, `test_*.py` next to source |
| Integration | vitest / pytest with test DB | Route + service against real Supabase local instance | `*.spec.ts`, `test_*_integration.py` |
| E2E | Playwright | Full user journey | `e2e/*.e2e.ts` at frontend root |

### Rules
- **Tests written before implementation** using acceptance criteria from FEATURES.md
- **A feature is not `complete` until tests pass** and `/feature-check` reports no missing coverage
- **No mocking of Supabase in integration tests** — use `supabase start` for a local instance
- **E2E tests hit real backend via Playwright's page** — not a mock; run against `npm run dev` for both apps
- **Snapshot tests only for stable UI primitives** — never for evolving business components

---

## Git

### Branch naming
- `feat/FEAT-NNN-short-description` — new feature from FEATURES.md
- `fix/short-description` — bug fixes
- `chore/short-description` — dependency updates, tooling, config
- `docs/short-description` — .agent/ or docs/ edits
- `refactor/short-description` — no behaviour change

### Commit format
```
<type>(<scope>): <summary> [<agent-tag>]
```

- **type:** `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `infra`
- **scope:** `web`, `api`, `schema`, `docs`, `ci`, or `FEAT-NNN`
- **summary:** imperative, lowercase, no trailing period, <72 chars
- **agent-tag:** one of `[claude-code]`, `[claude-design]`, `[gemini]`, `[codex]`

Examples:
```
feat(api): add /ingest endpoint with docling parse [claude-code]
fix(web): resolve chat scroll jump on new message [claude-design]
chore(api): pin voyageai to 0.3.4 [gemini]
test(api): add citation verifier edge cases [codex]
docs(schema): add rls policy for figures storage bucket [claude-code]
```

### PR requirements
- Every PR touches at most one feature (FEAT-NNN)
- Every PR that changes behavior updates CHANGELOG.md
- Every PR that changes SCHEMA.md or ARCHITECTURE.md locked decisions has human sign-off in the description
- No PR merges without `/gap-check` clean

### Never do
- Commit directly to `main`
- Commit `.env` files or real API keys
- Force-push shared branches
- Rewrite history on `main`

---

## Do not

Explicit forbidden patterns. `/gap-check` looks for these:

- `TODO` or `FIXME` in committed code without a linked FEATURES.md entry
- Magic numbers — extract to named constants
- Any-typed values in TS (`: any`) except at explicit API boundaries with a comment explaining why
- `# type: ignore` in Python without a comment explaining why
- Direct Supabase queries from `apps/web` for user-owned tables that could go through `apps/api` — the frontend should be a thin client
- Business logic in Next.js API routes — those are proxies only
- Skipping `/api-check` before writing external API code
- Storing API keys anywhere other than env vars
- Committing before running `/gap-check` locally

---

## Package management

- Frontend: `pnpm` preferred over `npm` or `yarn`. Lockfile committed.
- Backend: `uv` (fast, modern) preferred over `pip`. `pyproject.toml` + `uv.lock` committed.
- **Never auto-install packages.** Agent lists what it wants, human confirms.
- Pin exact versions for production dependencies. Ranges OK for dev dependencies.
