# Gap Check — 2026-07-24T11:37:35Z

<!-- MANUAL ENTRIES BELOW — preserved across /gap-check runs, edit freely -->

## Resolved — external reviews

### 2026-07-22 — Codex independent review of FEAT-000 through FEAT-003 (reviewed commit `d06fca4`)

Six findings against `apps/api/middleware/auth.py` and `apps/api/tests/test_auth.py`. All resolved same day, commit pending. Source: `.agent/reviews/2026-07-22.md`.

- [x] **WARNING — `exp` not required.** A correctly-signed token with no `exp` claim would pass; only the expired-*value* case was tested. **Fix:** `options={"require": ["exp"]}` added to `jwt.decode()`. **Test:** `test_missing_exp_claim_returns_401`.
- [x] **WARNING — `iss` not validated.** A validly-signed token from any other issuer/project would be accepted if its signing key was resolvable via the configured JWKS client. **Fix:** `issuer=self.issuer` passed to `jwt.decode()` (derived from `SUPABASE_URL`, or injectable via constructor); PyJWT requires the claim once `issuer=` is supplied. **Tests:** `test_wrong_issuer_returns_401`, `test_missing_issuer_returns_401`.
- [x] **WARNING — `aud` validation explicitly disabled** (`options={"verify_aud": False}`). Any audience, or none, was accepted. **Fix:** removed that option; `audience="authenticated"` passed to `jwt.decode()` instead (Supabase's documented audience for user access tokens). **Tests:** `test_wrong_audience_returns_401`, `test_missing_audience_returns_401`.
- [x] **INFO (flagged as source-derived, not test-proven) — unknown `kid` handling.** The fake JWKS client always returned one key regardless of the token's `kid`, so fail-closed behavior on an unknown `kid` was never actually exercised. **Fix:** `FakeJWKClient` now resolves keys from a `{kid: key}` dict and raises `jwt.PyJWKClientError` (caught by the same `except jwt.PyJWTError` branch as before — no middleware code change needed here, only the test double). **Test:** `test_unknown_kid_returns_401`.
- [x] **WARNING — error messages leaked which validation stage failed** (missing header vs. malformed vs. expired vs. missing `sub` all had distinct message text). **Fix:** every failure path now returns the same `GENERIC_UNAUTHORIZED_MESSAGE` ("Missing or invalid JWT"), matching API_CONTRACT.md's stated envelope. **Verified by:** every negative test in `test_auth.py` now asserts the exact generic message via a shared `assert_generic_401()` helper, not just the status code.
- [x] **INFO (asked for explicit proof) — algorithm confusion.** Review confirmed hardcoded `algorithms=["ES256"]` already closes this, but nothing tested it. **Test added:** `test_algorithm_confusion_hs256_with_ec_public_key_rejected` — forges an HS256 token using the EC public key's PEM bytes as the HMAC secret (built by hand with raw `hmac`/base64url, since `jwt.encode()` itself refuses to sign HS256 with PEM-shaped key material — a good guard rail, but it meant the attack payload had to be constructed manually to actually exercise the *decode*-side defense). Confirmed rejected.

**Verification beyond the tests:** ran the pre-fix decode options (`verify_aud=False`, no `issuer`, no `require: exp`) against a token with a wrong `iss`, wrong `aud`, and no `exp` claim at all — it was accepted. Ran the same token through the fixed options — rejected with `"Token is missing the \"exp\" claim"`. This confirms the new tests catch a real, previously-exploitable gap rather than passing vacuously.

**Also fixed while here (not a review finding, but required to make this log durable):** `gap-check.sh` used to fully overwrite `GAPS.md` on every run, which would have silently deleted this exact section the next time `/gap-check` ran. It now preserves everything below the `MANUAL ENTRIES BELOW` marker across runs.

**Not addressed, out of scope for this pass:** the review's remaining INFO-level notes (health/error-envelope shape, logging hygiene, migration-policy `qual`/`with_check` expression checks) were not findings requiring code changes — see `.agent/reviews/2026-07-22.md` for full detail if revisiting.

## Live RLS/storage enforcement verification (FEAT-001)

### 2026-07-22 — CRITICAL gap found by live-testing, not by static review: missing table grants

The Codex review above flagged (WARNING) that RLS enforcement had never been proven live, only checked for policy *presence*. Ran a real local Supabase instance (`supabase start` + Docker) to close that gap directly, and it surfaced something the static check couldn't have caught: **`20260722_001_initial.sql` created RLS policies but never granted the underlying table-level privileges** (`SELECT`/`INSERT`/`UPDATE`/`DELETE`) to `anon`/`authenticated`/`service_role`. Postgres requires both a GRANT and a matching policy — RLS alone doesn't unlock a table a role has no base privilege on. This blocked everything, including `service_role` (which has `BYPASSRLS` — irrelevant here; GRANT and RLS-bypass are independent permission layers). First symptom: a service-role `INSERT` into `documents` failed with `permission denied for table documents` before any RLS logic was even reached.

- [x] **Fix:** new migration `apps/api/migrations/20260722_002_grant_table_privileges.sql`, granting exactly what each table's existing policies imply (`authenticated`: full CRUD on `documents`/`conversations`, `SELECT`-only on `chunks`/`messages`/`citations`; `service_role`: full CRUD everywhere; `anon`: nothing). Applied and verified locally.
- [x] **Live proof, all four checks pass** (fresh local instance, real HTTP calls through PostgREST/GoTrue/Storage, not mocks):
  1. **Cross-user isolation:** inserted a `chunks` row as user A via service-role; queried as user B's authenticated session (real login, real JWT) — `200`, `0` rows. Clean RLS row-filtering.
  2. **Authenticated INSERT into `chunks` by a non-owner:** `403`, `permission denied for table chunks`. Note: this specific rejection comes from the table GRANT layer (only `SELECT` was granted to `authenticated` on `chunks`, matching the "no user-facing insert" design) — it never reaches the RLS policy layer at all, which is arguably a *stronger* guarantee than an RLS-only rejection would be.
  3. **Fully anonymous (no user JWT) INSERT into `chunks`:** `401`, `permission denied for table chunks` — `anon` role has zero table privileges here by design.
  4. **Authenticated write to the `figures` storage bucket:** `400` / `"new row violates row-level security policy"` — this one *is* a genuine RLS-policy rejection (figures has a `select`-only policy, no insert policy), giving one real example of the RLS layer itself firing, not just the grant layer.
- [x] `test_migrations.py` run for real against the local instance: **14/14 passed, 0 skipped** (previously 14/14 skipped — no local Postgres reachable).

**RESOLVED on the live project as of the FEAT-004 session (2026-07-22).** `20260722_002_grant_table_privileges.sql` has been applied to the live Supabase project (`nbrfjbjjjhawscncshdz`) via the dashboard SQL editor, same as `001`. Not independently re-verified with the live-enforcement script from this entry (that was only run locally) — worth doing before FEAT-007+ (`/ingest`, `/query`) writes real data there for the first time.

**Local Supabase stack:** left running (`http://127.0.0.1:54321`, DB on `:54322`) rather than torn down — useful for FEAT-004+ integration tests per STANDARDS.md ("No mocking of Supabase in integration tests — use `supabase start`"). Stop with `npx supabase stop` from `apps/api/` if not wanted.
