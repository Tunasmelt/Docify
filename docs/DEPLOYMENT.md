# Deployment runbook

Docify runs on three free-tier services. This page covers how they're wired, how to ship a change, and how to confirm it actually shipped. Incident history is in `CHANGELOG.md` (2026-07-27, 2026-08-09, 2026-08-18).

| Piece | Host | URL |
|---|---|---|
| Web (`apps/web`) | Vercel project `docify-web` | https://docify-web-steel.vercel.app |
| API (`apps/api`) | Render web service `docify-api`, **Docker runtime** | https://docify-api.onrender.com |
| Database / Auth / Storage | Supabase (production project) | — |

---

## Shipping a change

1. **Schema first.** If the change adds a migration, apply it to production *before* deploying code that depends on it: open the Supabase dashboard → SQL editor, paste the file, and run it. Apply migrations in filename order, each exactly once. Then add the row to `.agent/SCHEMA.md` §Migration log.
2. **Merge to `master`.** Both Vercel and Render auto-deploy from `master` on push.
3. **Verify the API deploy.** Render reporting "live" is not proof the new code is running:
   ```bash
   curl -s https://docify-api.onrender.com/health
   # {"status":"ok","version":"0.1.0","timestamp":"…","commit":"<sha>"}
   ```
   `commit` must equal the SHA you just merged. The first request after ~15 minutes idle wakes the instance and can take about a minute.
4. **Verify the web deploy.** Check the Vercel deployment for that commit succeeded, then load the site.
5. **Smoke test the core path**, especially after touching ingest, parsing, or dependencies: sign up (or use a demo account), upload a small PDF, wait for `ready`, ask a question, and confirm cited answers. Delete the test account afterwards (Settings → Danger zone).

---

## API (Render)

- **Runtime must be Docker.** The image installs `tesseract-ocr` with `apt-get`, which Render's native Python runtime can't do. Build config: root directory `apps/api`, Dockerfile `./Dockerfile`.
- **The Dockerfile copies source with an explicit list** (`main.py errors.py rate_limit.py`, `db/ middleware/ models/ routes/ services/`). **A new top-level module or package must be added to that list**, or the image builds fine and then crash-loops with `ModuleNotFoundError` (this happened with `rate_limit.py` and went unnoticed for 32 commits).
- **Memory: 512MB.** The parser is designed to fit (FEAT-027). Before merging dependency or parser changes, test under the same cap:
  ```bash
  cd apps/api
  docker build -t docify-api .
  docker run --rm --memory=512m -p 8000:8000 --env-file .env docify-api
  ```
- **Environment variables** (set in the Render dashboard, never committed): `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `VOYAGE_API_KEY`, `GEMINI_API_KEY`, `OCR_SPACE_API_KEY`, `FRONTEND_ORIGINS` (the Vercel URL; comma-separate multiple origins). `SUPABASE_JWT_SECRET` and `TESSERACT_CMD` are not needed. `RENDER_GIT_COMMIT` and `PORT` are injected by Render.
- **Free-tier behaviour:** the instance sleeps after ~15 minutes idle and loses all in-memory state. Per-minute rate limits reset on restart (harmless), while daily limits persist in Postgres. A restart kills any in-flight ingest; the stuck-document reaper marks it `failed` within 30 minutes, and users recover it with re-index.
- **If a pushed commit never deploys:** Render's GitHub webhook can go stale. Re-saving the service's branch setting (or a `PATCH` to its branch config via the API) forces a resync.

## Web (Vercel)

- Git-linked to `Tunasmelt/Docify`, root directory `apps/web`. Vercel detects pnpm from `pnpm-lock.yaml`; no build overrides are needed.
- **Environment variables:** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (production project), and `NEXT_PUBLIC_API_URL=https://docify-api.onrender.com`. `NEXT_PUBLIC_*` values are inlined at build time, so redeploy after changing them.
- Vercel's default deployment protection (SSO) is disabled so the `.vercel.app` URL is public. Re-check this if the project is ever recreated.
- `pnpm build` fails on ESLint errors, so run it locally before merging.

## Supabase (production)

- Migrations are applied manually via the SQL editor (see step 1 above). The CLI migration history is intentionally empty.
- **Auth → URL configuration:** the site URL and redirect allowlist must include the Vercel URL, or password-reset and OAuth redirects fail.
- **Google OAuth:** the provider must be configured with a real Google client. A live end-to-end sign-in has not yet been verified (MEMORY.md §Open questions).
- The service-role key bypasses RLS. It belongs only in Render's environment, never in Vercel or any `NEXT_PUBLIC_*` variable.

---

## Rollback

- **API:** in Render, redeploy a previous successful deploy. Check `GET /health` afterwards.
- **Web:** in Vercel, promote a previous deployment to production.
- **Schema:** each migration documents its reversal in a trailing `-- ROLLBACK` comment. Run it in the SQL editor only after rolling the code back, since newer code may depend on the newer schema.
