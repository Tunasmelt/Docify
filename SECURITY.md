# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through GitHub: **Security → Report a vulnerability** on this repository. Include the affected endpoint or file, steps to reproduce, and the impact you observed. You should get an acknowledgement within a few days.

In scope: the code in this repository and the deployed demo (https://docify-web-steel.vercel.app, https://docify-api.onrender.com). Please test only against accounts you created yourself, and don't run load or denial-of-service tests against the free-tier demo.

## Security model, in brief

- **Auth:** Supabase Auth issues ES256 JWTs. The API verifies them against Supabase's JWKS, requiring `exp`, `iss`, and `aud=authenticated` (`apps/api/middleware/auth.py`).
- **Tenant isolation has two layers, and which one applies depends on the path:**
  - Browser → Supabase (anon key + user JWT) is restricted by Postgres row-level security and Storage policies.
  - Browser → FastAPI → Supabase uses the **service-role key, which bypasses RLS**. On that path, every query is scoped by the JWT-derived `user_id` in application code. Ownership lookups return the same `404` for "doesn't exist" and "not yours".
- **Uploads:** `/ingest` only reads files under the caller's own `uploads/{user_id}/` prefix.
- **Secrets** live only in host environment variables (Render, Vercel). The service-role key is never sent to the browser.
- **Rate limits** protect the shared vendor quotas (`apps/api/rate_limit.py`).

Details: [`.agent/ARCHITECTURE.md`](.agent/ARCHITECTURE.md) §Multi-tenancy, [`.agent/SCHEMA.md`](.agent/SCHEMA.md) §Row-Level Security. Past security reviews are in [`.agent/reviews/`](.agent/reviews/).

## Known limitations

- An access token that was already issued keeps verifying until it expires, even after sign-out or account deletion. Verification is stateless, but refresh tokens are revoked immediately.
- Email-change confirmation links complete on an unauthenticated GET (Supabase platform behaviour). As a mitigation, starting an email change requires the current password.
