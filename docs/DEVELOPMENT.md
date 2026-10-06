# Local development

How to run Docify end to end on your machine: the local Supabase stack, the API, the web app, and the tests. For the system design see [`.agent/ARCHITECTURE.md`](../.agent/ARCHITECTURE.md); for conventions see [`.agent/STANDARDS.md`](../.agent/STANDARDS.md).

## Prerequisites

| Tool | Used for |
|---|---|
| Node 20+ and `pnpm` | `apps/web` |
| Python 3.12 and [`uv`](https://docs.astral.sh/uv/) | `apps/api` |
| Docker + [Supabase CLI](https://supabase.com/docs/guides/local-development) | Local Postgres/Auth/Storage (`supabase start`) |
| `psql` | Applying migrations locally |
| Tesseract (`tesseract-ocr`) | OCR tier 3 — `apt-get install tesseract-ocr` (Debian/Ubuntu), `brew install tesseract` (macOS), `winget install UB-Mannheim.TesseractOCR` (Windows; then set `TESSERACT_CMD`) |

Tesseract is only needed when a scanned PDF exhausts the first two OCR tiers; everything else works without it.

You also need real API keys for **Voyage** and **Gemini** (both have free tiers). An **OCR.space** key is optional; the public `helloworld` demo key works for ad hoc testing.

## 1. Start local Supabase

```bash
cd apps/api
supabase start            # prints the local URLs and demo keys
```

| Service | URL |
|---|---|
| API (PostgREST/Auth/Storage) | `http://127.0.0.1:54321` |
| Postgres | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| Studio | `http://127.0.0.1:54323` |
| Mailpit (captured auth emails) | `http://127.0.0.1:54324` |

## 2. Apply migrations

Migrations live in `apps/api/migrations/` and are **not** applied automatically by `supabase start`. Apply every `YYYYMMDD_NNN_*.sql` file in filename order:

```bash
cd apps/api
for f in $(ls migrations/2*.sql | sort); do
  psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -v ON_ERROR_STOP=1 -f "$f"
done
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -f migrations/verify_20260722_001.sql   # every row should say OK
```

After `supabase db reset` (which wipes the database), apply them again. See [`.agent/SCHEMA.md` §Migration log](../.agent/SCHEMA.md) for what each one does.

## 3. Configure environment

**`apps/api/.env`** (copy from `.env.example`):

| Variable | Local value |
|---|---|
| `SUPABASE_URL` | `http://127.0.0.1:54321` |
| `SUPABASE_SERVICE_ROLE_KEY` | `service_role key` from `supabase status` |
| `SUPABASE_JWT_SECRET` | unused — leave blank |
| `VOYAGE_API_KEY`, `GEMINI_API_KEY` | your keys |
| `OCR_SPACE_API_KEY` | optional |
| `TESSERACT_CMD` | only if `tesseract` isn't on `PATH` |
| `FRONTEND_ORIGINS` | leave blank (defaults to both `127.0.0.1:3000` and `localhost:3000`) |

**`apps/web/.env.local`** (copy from `.env.example`):

| Variable | Local value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | `http://127.0.0.1:54321` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `anon key` from `supabase status` |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` |

Never commit `.env` / `.env.local`. Keep production keys out of local files.

## 4. Run the apps

```bash
# terminal 1
cd apps/api && uv sync && uv run uvicorn main:app --reload     # http://localhost:8000/health

# terminal 2
cd apps/web && pnpm install && pnpm dev                        # http://127.0.0.1:3000
```

> **Open the web app at `http://127.0.0.1:3000`, not `localhost:3000`.** Supabase Auth's redirect allowlist (`apps/api/supabase/config.toml` → `site_url`) is pinned to `127.0.0.1`. From `localhost`, password-reset and OAuth redirects silently land on the wrong page.

Sign up with any email; confirmation and password-reset emails arrive in Mailpit (`http://127.0.0.1:54324`). Google OAuth is not configured locally.

## 5. Run the tests

```bash
cd apps/api && uv run pytest            # unit + integration
cd apps/web && pnpm e2e                 # Playwright (starts `pnpm dev` itself)
cd apps/web && pnpm build               # production build — runs ESLint as a hard gate
```

- Backend integration tests talk to the local Supabase stack, and conftest points the app at it automatically. If the stack isn't running they **skip** rather than fail, so a green run without `supabase start` is not full coverage.
- E2E tests need local Supabase **and** the API running (terminal 1 above), with migrations applied. They create and delete their own throwaway users.
- Backend tests fake Voyage/Gemini by default. A few live-API tests are opt-in via `RUN_REAL_*=1` env vars (see the `skipif` markers in `tests/`). Tests that need the public internet (e.g. the Voyage tokenizer download) carry the `network` marker and are deselected by default; run them with `uv run pytest -m network`. E2E chat and upload tests go through the real running API, so they **do** use Voyage/Gemini free-tier quota (Voyage allows ~3 requests/minute).
- Before calling a multi-file change done, verify from a fresh clone (`git stash -u` or a new `git clone`). The working tree can hide files you never committed (STANDARDS.md §Testing).

## Contributing

- Branch from `master` using `feat/FEAT-NNN-…`, `fix/…`, `docs/…` (STANDARDS.md §Git); never commit to `master` directly.
- Commit format: `<type>(<scope>): <summary> [<agent-tag>]`.
- A PR that changes behaviour updates `CHANGELOG.md`; schema changes add a migration plus a row in SCHEMA.md's migration log; API changes update `.agent/API_CONTRACT.md` and `apps/web/lib/types/` together.
- CI (`.github/workflows/ci.yml`) runs the backend suite against a fresh local Supabase stack, plus web lint, typecheck and build, on every PR. Run the commands in step 5 locally first. CI doesn't run the Playwright e2e suite.
