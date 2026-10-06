# Standards

Coding standards and conventions for this project. Violations are flagged in GAPS.md by `/gap-check`.

---

## Naming

### Files & folders
- Frontend: `kebab-case.tsx` for files, `PascalCase` for component names within them
  - `components/chat/message-bubble.tsx` exports `MessageBubble`
- Backend Python: `snake_case.py` for files, `snake_case` for functions, `PascalCase` for classes
  - `services/embedder.py` exports `class Embedder:` with `def embed(...)`
- Migrations: `YYYYMMDD_NNN_short_description.sql` — e.g. `20260722_001_initial.sql`

### Symbols
- **Constants:** `SCREAMING_SNAKE_CASE` in both TS and Python
- **Types (TS):** `PascalCase` — `type Citation = {...}`, `interface DocumentRow {}`
- **React components:** `PascalCase`, one component per file (except tiny helpers)
- **Hooks:** `useSomething` prefix
- **Python classes:** `PascalCase`, one class per file for services
- **Test files:**
  - Frontend: `foo.e2e.ts` for Playwright e2e (`apps/web/e2e/`). There is no frontend unit-test runner yet; if one is added, use `foo.test.ts`
  - Backend: `test_foo.py` for pytest

### Database
- **Tables:** plural `snake_case` — `documents`, `chunks`, `citations`
- **Columns:** `snake_case` — `user_id`, `created_at`, `page_number`
- **Enums:** singular `snake_case` — `document_status`, `element_type`
- **Indexes:** `{table}_{column(s)}_idx` — `chunks_user_idx`, `documents_status_idx`
- **RLS policies:** `{table}_{operation}` — `documents_select`, `chunks_insert`

### Env vars
- `SCREAMING_SNAKE_CASE`
- Every variable the code reads must appear in that app's `.env.example`. Current set:
  - `apps/api`: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` (legacy, unused), `VOYAGE_API_KEY`, `GEMINI_API_KEY`, `OCR_SPACE_API_KEY`, `TESSERACT_CMD` (optional), `FRONTEND_ORIGINS` (comma-separated CORS origins). `RENDER_GIT_COMMIT` is injected by Render, never set by hand
  - `apps/web`: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`
- Every app has `.env.example` at its root listing all required vars

---

## Structure

### Frontend directory layout
The browser calls FastAPI directly via `lib/api/`'s `apiFetch()` — there is no Next.js proxy layer.
```
apps/web/
├── app/                       Next.js App Router pages
│   ├── (auth)/                unauthenticated pages
│   ├── (app)/                 authenticated pages (protected by middleware)
│   ├── auth/callback/         Supabase Auth PKCE callback (the one real route handler
│   │                          in this app — not a FastAPI proxy)
│   └── api/                   placeholder only — no Next.js API routes exist
├── components/
│   ├── ui/                    shadcn primitives — never modify these directly
│   ├── {feature}/             feature-scoped components
│   └── layout/                nav, sidebar, shell
├── lib/
│   ├── supabase/              client wrappers (browser talks to Supabase directly for
│   │                          Auth/Storage — not proxied through FastAPI either)
│   ├── api/                   FastAPI client — called directly from the browser
│   └── types/                 shared TS types
└── middleware.ts              auth gate for (app)/* — route guard only, never proxies
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
- **Every service-role query is scoped by the JWT-derived `user_id`.** The service role bypasses RLS, so this filter is the tenant boundary for FastAPI — a query without it is a cross-tenant leak (SCHEMA.md §Service-role client discipline)
- **Route handlers that call the Supabase client or any vendor SDK are plain `def`, never `async def`.** Those clients block; in an `async def` handler they freeze the event loop for every user. An `async def` route must `await asyncio.to_thread(...)` for each blocking call (`tests/test_event_loop.py` enforces this)
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

Every feature has tests at the levels that apply:

| Level | Tool | What it tests | Where |
|---|---|---|---|
| Unit | pytest | Pure functions, fakes for vendor APIs | `apps/api/tests/test_*.py` |
| Integration | pytest + local Supabase (`supabase start`) | Routes, RPCs, RLS, Storage against a real local stack (skipped if it isn't running) | `apps/api/tests/test_*.py` |
| E2E | Playwright | Full user journeys against both apps + local Supabase | `apps/web/e2e/*.e2e.ts` |

Run: `cd apps/api && uv run pytest` · `cd apps/web && pnpm e2e`. See `docs/DEVELOPMENT.md` for the local stack.

### Rules
- **Tests written before implementation** using acceptance criteria from FEATURES.md
- **A feature is not `complete` until tests pass** and `/feature-check` reports no missing coverage
- **No mocking of Supabase in integration tests** — use `supabase start` for a local instance
- **E2E tests hit real backend via Playwright's page** — not a mock; run against `npm run dev` for both apps
- **Snapshot tests only for stable UI primitives** — never for evolving business components
- **Verify from a fresh clone before closing a multi-file feature.** `pytest`, `tsc`, `next dev`, and Playwright all run against the working tree and cannot tell that a file was never committed. Clone (or `git stash -u`), install, build, and test (MEMORY.md §Anti-patterns 2026-08-07).
- **Frontend verification includes `next build`.** It runs ESLint as a blocking gate; `next dev` only warns and `tsc --noEmit` skips ESLint.
- **Verify deploys, not just commits.** After pushing a backend fix, check `GET /health`'s `commit` matches (MEMORY.md §Anti-patterns 2026-08-18).

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
- **agent-tag:** the agent that made the commit — in practice `[claude-code]`; `[codex]` for external review fixes

Examples:
```
feat(api): add /ingest endpoint with pdfplumber parse [claude-code]
fix(web): resolve chat scroll jump on new message [claude-code]
chore(api): bump voyageai to 0.3.7 [claude-code]
test(api): add citation verifier edge cases [codex]
docs(schema): add rls policy for figures storage bucket [claude-code]
```

### PR requirements
- Every PR touches at most one feature (FEAT-NNN)
- Every PR that changes behavior updates CHANGELOG.md
- Every PR that changes SCHEMA.md or ARCHITECTURE.md locked decisions has human sign-off in the description
- No PR merges without `/gap-check` clean
- CI (`.github/workflows/ci.yml`) must be green: backend `pytest` against local Supabase, web lint, `tsc` and build. Run `uv run pytest` and `pnpm build` locally before opening a PR

### Never do
- Commit directly to `master`
- Commit `.env` files or real API keys
- Force-push shared branches
- Rewrite history on `master`

---

## Do not

Explicit forbidden patterns. `/gap-check` looks for these:

- `TODO` or `FIXME` in committed code without a linked FEATURES.md entry
- Magic numbers — extract to named constants
- Any-typed values in TS (`: any`) except at explicit API boundaries with a comment explaining why
- `# type: ignore` in Python without a comment explaining why
- Direct Supabase queries from `apps/web` for user-owned tables (`documents`, `chunks`, `conversations`, `messages`, `citations`) that could go through `apps/api` — the frontend should be a thin client. (Pure Supabase Auth/Storage operations — login, session management, avatar upload — are the one legitimate exception; those go directly from the browser to Supabase by design, see ARCHITECTURE.md's System diagram.)
- Business logic in Next.js API routes. None exist today; if one is ever added it must stay a thin proxy (auth check + forward).
- Skipping `/api-check` before writing external API code
- Storing API keys anywhere other than env vars
- Committing before running `/gap-check` locally

---

## Package management

- Frontend: `pnpm` preferred over `npm` or `yarn`. Lockfile committed.
- Backend: `uv` (fast, modern) preferred over `pip`. `pyproject.toml` + `uv.lock` committed.
- **Never auto-install packages.** Agent lists what it wants, human confirms.
- Backend: bound every dependency to a compatible range in `pyproject.toml` (`>=x.y,<x.z`), with a comment when a floor exists for a security fix. Exact versions come from `uv.lock`.
- Frontend: framework packages whose minor versions must match (`next`, `eslint-config-next`, `@supabase/*`, `@playwright/test`) are pinned exactly; others use `^`. Exact versions come from `pnpm-lock.yaml`.
- Lockfiles are always committed and regenerated with the tool (`uv lock`, `pnpm install`), never edited by hand.

## Tooling gaps

- Python has no linter/formatter configured (`ruff` is only in `.gitignore`). Until one is added, match the surrounding style.
- CI does not run the Playwright e2e suite (it needs the API running and uses real Voyage/Gemini quota).
