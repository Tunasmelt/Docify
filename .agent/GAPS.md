# Gap Check — 2026-10-07T17:29:41Z

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

## FEAT-013 self-audit fixes (2026-07-24) — OAuth completion untestable in this environment

The self-audit found `signInWithOAuth("google")` had no callback route at all (item 1) and
`resetPasswordForEmail` links landed on a dead-end login form (item 2) — both share the exact
same root cause (PKCE code-exchange), both fixed by `app/auth/callback/route.ts` +
`app/account/update-password/page.tsx`. The password-recovery half is fully live-verified
end-to-end (real Mailpit email → real link → real code exchange → real password update → real
login with the new password — `apps/web/e2e/password-reset.e2e.ts`).

- [ ] **NOT independently verifiable here: completing a real Google OAuth sign-in.** Two separate
  blockers, neither fixable from this environment: (1) the local Supabase project has no
  `[auth.external.google]` client configured at all — `curl .../authorize?provider=google` returns
  `"Unsupported provider: provider is not enabled"` before ever reaching Google; (2) even with a
  real client configured, completing Google's actual consent screen requires a real Google account
  and can't be automated by an agent (Google actively blocks headless/scripted logins). What WAS
  proven live: the callback route's PKCE code-exchange mechanism itself, using the identical
  contract OAuth and password-recovery both share (confirmed via `flowType: "pkce"` in
  `@supabase/ssr`'s installed source) — exercised end-to-end through the recovery flow, and the
  callback route's error path was separately exercised with a bogus code
  (`password-reset.e2e.ts`'s second test).
  **Action needed before shipping OAuth:** once a real Google OAuth client is configured (likely
  at deploy time, per FEAT-013's original scope), manually click through "Continue with Google" in
  a real browser once and confirm: (a) Google's consent screen appears, (b) after approving, the
  browser lands on `/documents` with a real session (check dev tools → Application → Cookies for
  `sb-*-auth-token`), (c) a second visit to `/login` while that session is active redirects
  straight back to `/documents` (proving the session persisted, not just the initial redirect).
  Also add the deployed domain to `config.toml`'s (or the hosted project's)
  `additional_redirect_urls` — the callback route's `next` param handling doesn't need changes for
  this, only GoTrue's own allowlist does.

**Also worth knowing (not a code bug, a local-dev-environment trap):** GoTrue's redirect-URL
allowlist (`site_url`/`additional_redirect_urls` in `config.toml`) is pinned to `127.0.0.1:3000`.
Accessing the local dev server via `http://localhost:3000` instead of `http://127.0.0.1:3000`
causes GoTrue to silently reject the app's `redirectTo` and fall back to a bare `site_url`
redirect with no path — this exact thing broke the password-reset e2e test the first time it ran,
traced via the redirect chain's real `Location` headers, not guessed. `playwright.config.ts` now
pins `baseURL`/`webServer.url` to `127.0.0.1:3000` for this reason. If accessing the app manually
in a browser during local dev, use `http://127.0.0.1:3000`, not `http://localhost:3000`, or
password-reset/OAuth redirects will silently misbehave.

## FEAT-014 Documents UI wiring (2026-07-25) — delete does not block during 'embedded' status

While live-testing the Documents UI's real delete flow, a genuine `chunks_document_id_fkey`
foreign-key violation surfaced in the backend logs: `run_ingest_pipeline`'s background task tried
to insert chunk rows for a `document_id` that no longer existed in `documents` — the row had
already been deleted while the pipeline was still mid-flight.

- [x] **RESOLVED 2026-07-25, commit `4a4c73e` ("fix(FEAT-008): block DELETE during 'embedded'
  status, not just 'parsing'") — closed out properly 2026-08-07 during the docs-reconciliation
  pass (this entry was still marked open, a real doc-drift bug of its own, not silently deleted
  per this project's own convention of leaving a resolution note).** `DELETE /documents/{document_id}`'s 409 guard (`routes/documents.py`) only blocks
  `status == 'parsing'`.** But `run_ingest_pipeline` (`routes/ingest.py`) still has real
  in-flight work after the status flips to `'embedded'` — figure upload, the bulk `chunks`
  insert, and `mark_ready` all happen strictly after `mark_embedded()`. A delete landing in that
  window is not blocked, deletes the `documents` row (RLS/ownership checks pass — it's a real,
  currently-existing row at the time of the check), and the still-running background task's
  later `insert_chunks()` call then fails with a dangling foreign key. `_fail_document`'s cleanup
  path doesn't help here either — there's no document row left for it to mark `failed`.
  **How this was surfaced:** not through the normal single-document delete UI a real user would
  use, but by this session's own test-cleanup pattern (deleting a test *user* via the admin API
  immediately after starting an upload, to tear down test fixtures quickly) — `documents.user_id`
  cascades on `auth.users` delete, so the user-delete removed the document out from under its
  own still-running pipeline. A real user deleting one of their own documents through the normal
  UI *can* hit the identical race if their click lands during the `'embedded'` window specifically
  (a real, if narrow, timing window — not purely a test artifact), so this is worth fixing
  properly, not dismissing as test-only.
  **Fix actually applied (option 1 of the two suggested below):** the 409 guard was broadened to
  `status in ('parsing', 'embedded')` — confirmed still in place in the current code
  (`routes/documents.py`, `if document["status"] in ("parsing", "embedded"):`), not just applied
  and later reverted. The re-check-before-insert_chunks alternative (option 2) was not also
  built — the broadened guard alone closes the real race, since a delete can no longer land
  during either in-flight window at all.
  **Original suggested fix (for historical context — option 1 is what shipped):** broaden the
  409 guard to `status in ('parsing', 'embedded')`, or — more robustly — have
  `run_ingest_pipeline` re-check the document row still exists immediately before `insert_chunks`
  and treat a missing row as a clean, silent abort rather than letting the FK violation surface
  as an unhandled exception.
- [x] **Confirmed NOT a regression from this pass** — `run_ingest_pipeline` and
  `delete_document`'s code are both unchanged by FEAT-014 (a pure frontend wiring task); this gap
  predates it, just not exercised live until real delete requests started flowing from a real UI
  against real in-flight pipelines.

## FEAT-024 (2026-07-28, extended 2026-08-02) — two of SCOPE.md's three "Production job execution" facets now closed

`.agent/SCOPE.md`'s Phase 5 "Production job execution for `/ingest` (and `/query`)" entry has
tracked three facets of the same underlying problem since it was written: durability, a real
worker-pool/queue architecture, and rate-limiting. This real Render deploy — the first time this
app ran against real, live, shared-quota vendor APIs in production — surfaced concrete evidence
of exactly why the rate-limiting facet mattered (Voyage's real 3 RPM ceiling, Gemini's real
20/day `gemini-2.5-flash` ceiling, both confirmed live in earlier sessions per `.agent/MEMORY.md`)
and made it worth building ahead of the other two. **2026-08-02 update:** durability is now
closed too (lazy reaper + `POST /reindex`, below) — only worker-pool/queue architecture remains
open of the original three.

- [x] **Rate-limiting on `/ingest` and `/query` (+ `/query/stream`) — done.** `apps/api/rate_limit.py`,
  real vendor-quota-derived per-user limits, verified live end-to-end (`test_rate_limit.py`).
  Full reasoning and numbers in `.agent/FEATURES.md`'s FEAT-024 entry and `.agent/SCOPE.md`.
- [x] **Durability — closed 2026-08-02 (FEAT-024 follow-up), lazy/reactive approach, not a
  real worker-pool/queue.** Rate-limiting alone never addressed this — it bounds how much NEW
  load can *start*, not what happens to a request already in flight. Closed via two additive
  pieces: (1) `GET /documents` (`routes/documents.py`) opportunistically reaps any of the
  requesting user's own documents still stuck in `'parsing'`/`'embedded'` past a justified
  30-minute threshold (`STUCK_DOCUMENT_THRESHOLD_SECONDS`, `routes/ingest.py`) to `'failed'`,
  with an honest error message ("processing timed out, possibly interrupted by a service
  restart") — no scheduler, no cron, fires exactly when a user looks at their own document
  list; (2) `POST /reindex/{document_id}` (new, `routes/ingest.py`) re-runs the pipeline from
  the file already in Storage, recovering a reaped document (or any document, for other real
  reasons — re-embedding one with Gemini-fallback chunks once Voyage quota recovers, retrying
  FEAT-017's OCR chain on a pre-existing document).

  **Proven against the ACTUAL failure mode, not a hand-crafted row:**
  `apps/api/tests/test_reindex.py::test_reaper_and_reindex_recover_a_document_whose_pipeline_was_really_killed`
  interrupts a real call to `run_ingest_pipeline()` via a `BaseException` subclass mid-parse —
  deliberately NOT an `Exception`, so the function's own `except Exception: ... _fail_document(...)`
  cleanup block never runs, the same way a real SIGKILL/OOM-kill bypasses Python's exception
  handling entirely — confirms the document is left genuinely stuck at `'parsing'` with no error
  message (proving interruption, not a handled failure, actually happened — this is exactly the
  2026-07-27 OOM-crash's real observed shape), then drives the real `GET /documents` and
  `POST /reindex/{id}` HTTP routes end-to-end to confirm full recovery to `'ready'` with real
  chunk rows produced.

  **Explicit, stated tradeoff — this is NOT a real crash-recovery system:** no automatic retry,
  no dead-letter queue, no proactive detection (a document stays visibly stuck until a user's
  own `GET /documents` call happens to run past the threshold — could be seconds or days later
  depending on when they next look). Acceptable at this project's real scale (solo-dev
  portfolio project, one Render free-tier instance, no meaningful concurrent-user load) and for
  the failure this specifically closes — a rare backstop, not a load-bearing system. Full
  reasoning and the tradeoff's explicit justification: `.agent/SCOPE.md`'s "Production job
  execution" entry.
- [ ] **Worker-pool/queue architecture — still open, NOT addressed by durability's fix above.**
  Heavy Docling/Voyage work still runs on the request-serving process; no timeout/backpressure/
  per-user concurrency cap exists for a task that's genuinely HUNG (not crashed) — the lazy
  reaper only recovers a document once it's been stuck past the threshold, it does nothing to
  stop a runaway task from consuming resources indefinitely in the meantime. A real, separate,
  larger piece of work than either durability or rate-limiting.

## 2026-07-31 — Real production data-loss incident: null citation marker crashed `create_query_turn`, losing an entire conversation turn

**Report:** a real `POST /query/stream` turn (a broad "summarize the provided paper" request, not
a narrow factual question) failed to persist with `null value in column 'marker' of relation
'citations' violates not-null constraint`. Because `create_query_turn` (migrations/20260724_002,
20260725_002) is ONE atomic plpgsql function — the citation INSERT loop runs after the
user-question and assistant-answer message INSERTs in the same function body — any exception in
that loop rolled back the message rows too, losing the entire turn (the generated answer had
already streamed to the client's screen before the persist step failed). Directly connects to a
separate report that chat history "doesn't persist."

**Root cause — honestly reported, not overstated:** extensive live reproduction did NOT find a
path where the current `routes/query.py` `_extract_claim_spans()` + persist-loop logic actually
produces a null/unresolvable marker. Tested against 6+ real Gemini-generated broad-summarization
answers (single-turn, multi-topic, and conversation-history-carrying follow-ups, one real run
citing 20 distinct positions in dense nested-markdown-list prose) plus 8 hand-constructed
adversarial answer shapes fed directly through the real parsing functions (trailing standalone
`[N]` after the final sentence, leading brackets, markdown bullets/headers/numbered-list-as-
bracket formatting, bracket-only sentences) — every citation the client-facing `generate()` call
reports is already range-validated by `Generator._parse_citations`, and a position with no
resolvable claim-bearing sentence is already silently dropped before ever reaching the persist
loop (confirmed: this IS a real, separate, smaller bug — the dropped citation's `[N]` marker
stays visible in the delivered answer text with no matching `CitationResponse`, since
`dropped_positions` only tracks verified-UNSUPPORTED citations, not never-verified ones — logged
here, not separately fixed, since it's cosmetic, not data-loss).

**"Not reproduced today" is not "cannot happen," and this is exactly the class of bug (silent
citation-integrity corruption, not a crash) this project has adversarially audited hardest.**
Fixed defensively at both layers regardless of the unconfirmed exact trigger:
- [x] **Python:** `routes/query.py`'s new `_is_resolvable_marker()` guard, applied identically in
  `/query` and `/query/stream`, in BOTH the pre-verification loop (closes a previously-unguarded
  `IndexError` risk on `generator_chunks[position-1]` — `post_query` has no surrounding
  try/except past generation, unlike the streaming path) and the persist loop. A citation that
  fails the guard is dropped the same fail-safe way a hallucinated out-of-range marker already
  is — logged, never persisted, never returned to the client.
- [x] **SQL:** `migrations/20260731_002_citation_persistence_defensive.sql` — each citation
  INSERT inside `create_query_turn`'s loop is now wrapped in its own `BEGIN/EXCEPTION WHEN
  OTHERS` block. A malformed citation (null marker, or any other cause) is logged via `RAISE
  WARNING` and skipped; the message rows and every other valid citation in the same turn still
  commit. This is the structural fix that actually satisfies "one bad citation must never cost
  the whole turn," independent of whether the Python layer is ever proven airtight.
- [x] **Regression tests** (both layers, both endpoints): `test_query.py`'s
  `test_unresolvable_citation_position_is_dropped_not_crashed_rest_of_turn_persists` and
  `test_create_query_turn_sql_skips_a_malformed_citation_without_losing_the_turn`, mirrored in
  `test_query_stream.py` — use a hand-built `GenerateResult`/`GenerateStreamResult` to force the
  exact unresolvable-position shape directly (since it can't be reliably coaxed out of a live,
  non-deterministic model call), proving the turn survives with the valid citation intact and
  the bad one silently dropped, in both streaming and non-streaming.

**Follow-up worth doing, not done here (separate, smaller, cosmetic bug found along the way):**
the "dropped, no resolvable claim text" citation case leaves its `[N]` marker visible in the
answer text sent to the client with no matching citation object — should be added to
`dropped_positions`/`_strip_dropped_markers` the same way an UNSUPPORTED verdict already is, so
the visible answer never shows a dangling, unclickable marker. Not the data-loss bug reported
here; logged for whoever picks it up next.

## ACCEPTED, OPEN — email-change confirmation completes on a bare, unauthenticated link click; no full fix available at the platform layer

**Found:** 2026-08-05, independent audit (`.agent/reviews/2026-08-05-settings-audit.md`, item 2),
corrected and finalized same day (see `.agent/MEMORY.md`'s "Email-change confirmation" entry —
an earlier version of this finding overstated it as a session-hijack vulnerability, based on a
test client that didn't match the app's real PKCE configuration; that escalation is superseded).

**The real, remaining gap:** anyone with read access to *either* the account's old or new email
inbox — not both, despite `double_confirm_changes = true`'s name implying otherwise — can force
a permanent email-address change on the account with a single anonymous HTTP request, no
password, no session, no PKCE code-verifier needed for the swap itself (PKCE, which this app's
real client already correctly uses, only protects session-TOKEN issuance at a separate exchange
step — confirmed empirically, not assumed). This is real GoTrue server behavior for the
`email_change` verification type, deliberately supporting cross-device confirmation (the same
model signup/recovery/magic-link all share) — not a bug this project introduced.

**Why it isn't fully fixed:** investigated three angles for a native lever before accepting
this — (1) no `secure_email_change`-equivalent Supabase config flag exists (only
`secure_password_change`, for a different flow); (2) this Supabase CLI version exposes exactly
two Auth Hook points (`before_user_created`, `custom_access_token`), neither of which fires at
email-change-confirmation time; (3) `verifyOtp()` (the alternate, code-based confirmation path)
hits the same unauthenticated `/verify` endpoint with no session requirement built into the SDK
call — architecturally the same trust model as the link, just POST instead of GET. A complete
fix requires NOT using GoTrue's native email-change flow at all — a fully custom in-app OTP
(generate our own code, require the user to be signed in AND manually enter it, only then call
the admin API to perform the swap) — which was presented as an option and explicitly deferred in
favor of the lighter mitigation below, given real new-surface cost (code storage/expiry, rate
limiting, a new email template, UI, tests).

**Mitigation shipped instead (2026-08-05):** `lib/supabase/profile.ts`'s `requestEmailChange`
now requires the account's current password, verified via a real `signInWithPassword` call,
before a change can even be INITIATED (`email-section.tsx` gates the submit button on it,
`e2e/settings.e2e.ts` has a real test proving a wrong password blocks it and the account stays
untouched). **This raises the bar on who can START a change — it does not and cannot close the
gap in how a change gets CONFIRMED**, since the confirmation step happens independent of
whatever gated the initiation.

**Not addressed, deliberately deferred:** the full custom in-app OTP flow described above.
Revisit if this app's threat model changes (e.g. handling genuinely sensitive data where an
attacker gaining transient mailbox access to force an email swap — setting up a follow-on
password-reset takeover — is judged unacceptable even as a two-step chain rather than a one-click
exploit).
