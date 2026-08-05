# Settings/Profile Batches 1 & 2 — Independent Audit

**Date:** 2026-08-05
**Scope:** Settings batch 1 (profile, security) and batch 2 (preferences) — both uncommitted.
**Method:** Real infrastructure only (local Supabase, real backend, real Mailpit, real Playwright browser sessions). No code was modified as part of this review; a temporary e2e test written for item 3 was deleted after use, and one throwaway Python script per live test was written to `/tmp` (never committed).

---

> **CORRECTION (2026-08-05, same-day follow-up):** Item 2's original conclusion below overstates the severity of the email-change finding — it claims a full session-hijack/account-takeover-via-single-click, based on testing with a Python `supabase-py` client that defaults to `flow_type="implicit"`. This app's real client (`@supabase/ssr`'s `createBrowserClient`) defaults to `flowType: "pkce"`, confirmed in the installed package source, and was not the client used below. Retested with a real `@supabase/supabase-js` client configured identically to the app's: the session/token-hijack part of this finding **does not apply** — PKCE code exchange genuinely requires the matching `code_verifier`, confirmed by bypassing the SDK entirely and POSTing raw HTTP with a wrong/omitted verifier (`400 bad_code_verifier` / `400 validation_failed`). What **does** still hold, reconfirmed under the correct PKCE client: the email address itself still swaps on a bare, unauthenticated GET, independent of PKCE — a real but narrower gap than originally reported here. Full corrected writeup, root-cause investigation (no native Supabase/GoTrue fix exists), and the mitigation shipped in response: `.agent/MEMORY.md`'s "Email-change confirmation" entry (2026-08-05) and `.agent/GAPS.md`'s "ACCEPTED, OPEN" section. The text below is left unedited as the historical record of what this pass actually found and concluded at the time; treat the MEMORY.md/GAPS.md entries as authoritative over it.

---

## Summary table

| # | Item | Verdict |
|---|------|---------|
| 1 | Avatar RLS | **Confirmed correct.** Real 2-user boundary test: write/update/delete blocked for non-owner; SELECT policy scope confirmed harmless. |
| 2 | Email double-confirm | **Confirmed, and materially worse than reported.** Real account-takeover path exists — see below. **Priority finding.** |
| 3 | useCurrentUser | **Confirmed correct.** Real sign-out/sign-in-as-different-user test: no identity leak. |
| 4 | Sign-out-other-sessions | **Confirmed correct, stronger than documented.** Real refresh-token AND live-access-token revocation, immediate. |
| 5 | Rerank cost-guard | **Confirmed correct** via existing precise call-counting tests (composed across two test files). |
| 6 | Non-streaming path parity | **Confirmed correct — no drift.** Both routes share the same helper functions and Verifier; tests exist for both paths. **Priority finding.** |
| 7 | localStorage usage | **Confirmed correct.** No project rule against it; SSR-hydration-safe pattern verified by code reading. |
| 8 | Credential hygiene | **Confirmed clean**, within the limits of what this environment can show. |
| 9 | Regression | Backend suite and 4 of 6 named e2e suites pass fresh. 2 suites blocked by real, unrelated Gemini daily quota exhaustion — not re-verified today. |

---

## 1. Avatar RLS — independently re-verified

Ran a real two-user test directly against local Supabase Storage (not the app UI, not the policy SQL read in isolation):

- User A uploads `{uidA}/avatar`. **Confirmed real content stored.**
- User B attempts `upload(..., upsert=true)` on A's path → **403, "new row violates row-level security policy."**
- User B attempts `update()` on A's path → **403, same RLS rejection.**
- User B attempts `remove([...])` on A's path → call returns `[]` (Postgres-level RLS blocks the delete, no rows affected — not an exception, so I independently re-listed A's folder and re-downloaded the object afterward to confirm it **still exists, byte-identical to the original**). Trusting the empty-list return alone would have been a gap — confirmed via the actual object state, not the call's return value.
- User B's `list()` against the **`uploads`** bucket (a different, private bucket) for A's folder still returns empty — confirming the new `avatars_select` policy (`bucket_id = 'avatars'`, no owner check) does not leak into other buckets' RLS.

**SELECT policy scope check:** User B *can* `list()`/`download()` A's avatar via the authenticated Storage API (by design — the migration's own comment predicts this). Confirmed the exposed metadata (`name`, `id`, `created_at`/`updated_at`/`last_accessed_at`, `metadata.{eTag,size,mimetype,cacheControl}`) reveals nothing an anonymous, unauthenticated `GET` against the plain public URL doesn't already reveal — verified by hitting `/storage/v1/object/public/avatars/{uidA}/avatar` with zero auth and getting the identical bytes. The migration comment's claim ("not a privacy loosening: content is already public") is **empirically correct**, not just plausible.

No further action needed here.

---

## 2. Email double-confirm — PRIORITY FINDING, reproduced and materially escalated

### Reproducing the original finding

Confirmed `double_confirm_changes = true` in `apps/api/supabase/config.toml`. Created a real user, signed in, called `updateUser({ email: newEmail })` with the **exact** `emailRedirectTo` the app's `EmailSection.tsx` uses (`{origin}/auth/callback?next=/settings`). Two real emails arrived in Mailpit (old address + new address), each containing a link to `http://127.0.0.1:54321/auth/v1/verify?token=...&type=email_change&redirect_to=...`.

Visiting **either link alone**, with no interaction with the other, completes the email swap. This matches the original report.

### What the original report did not find

The link is **not** a PKCE `code` parameter, despite `lib/supabase/profile.ts`'s own comment claiming it is ("generates a confirmation link shaped identically to password-reset's — a `code` param, exchanged by ... `exchangeCodeForSession()`"). That comment is **incorrect**. Empirically, GoTrue's `/auth/v1/verify?type=email_change` endpoint:

1. Performs the email swap **server-side, synchronously, on a single anonymous `GET`** — confirmed via `admin.get_user_by_id()` showing the new email immediately after the `GET`, before any app code ever runs.
2. Responds with a **303 redirect carrying a live, valid session directly in the URL fragment**: `{redirect_to}#access_token=...&refresh_token=...&expires_in=3600&type=email_change`. No `code`, no PKCE, no code-verifier binding to the browser/session that initiated the change — this is the **implicit flow**, issuing real, usable JWTs to whoever GETs the link.

I verified this for **both** the old-address and new-address links independently — both hand out a live token pair.

### The actual security question: can an attacker who compromises only the new address take over the account?

**Yes — and it is a genuinely severe, standalone account-takeover primitive, not merely "the email changes with one click."**

The threat model the task asked about — an attacker who compromises *only* the new email address, never touching the old one — maps exactly onto a realistic scenario: the address a user is migrating *to* is exactly the kind of secondary/recovery mailbox most likely to be less well-defended (an old provider, a shared family inbox, a address the user re-used and forgot to secure), and it's also the one an attacker performing a **social-engineering-driven "update your email" request** would control from the start. For that attacker:

- A single anonymous `GET` on the link in their own inbox gets them `access_token` + `refresh_token` for **the victim's real account**, no code-verifier, no session binding to the victim's device, no further confirmation. This works via `curl`/`urllib`/any HTTP client — no browser needed.
- Separately, and more subtly: this app's own `/auth/callback/route.ts` only reads a `?code=` **query** parameter (line 26: `searchParams.get("code")`). A URL fragment (`#access_token=...`) is **never sent to any server** by a real browser — so a victim (or attacker) who clicks the link in an ordinary browser will *not* land on a working page in this app; the server-side route sees no `code`, falls through to its error branch, and redirects to `/login?error=This link is invalid or has expired.` This means the app's current UI happens to *look* broken for this flow — but that is not a mitigation. It only means the "one-click, land-in-the-app" path is accidentally broken; the underlying token issuance already happened at GoTrue, before the browser ever reached the app, and is fully independent of anything the Next.js route does or doesn't do with it. An attacker extracting the token pair directly from the `Location` header (exactly as I did, and exactly as any attacker with `curl`/devtools would) still gets a working session — they just need one extra step (`supabase.auth.setSession({access_token, refresh_token})` in their own browser, using the app's own public `NEXT_PUBLIC_SUPABASE_URL`/anon key) instead of the link "just working."

**Verdict: this is a real weakening of the app's account-security posture**, independent of and worse than the "single link suffices to change the email" framing the original report used. `double_confirm_changes = true`'s name/documentation implies a security property (both parties must agree) that this GoTrue version does not actually provide, and on top of that, either single link independently mints a full live session for the account — a session-hijack primitive, not just an email-swap primitive.

### Recommendation (reporting only — no code changed)

This needs a real fix before shipping, not a documentation update. Worth flagging explicitly to the user/next implementer: options include (a) switching the app's callback handling to also process the implicit/fragment flow via **client-side** JS (a page component, not a route handler, since fragments never reach the server) with `detectSessionInUrl` and immediately calling `signOut()` + forcing re-authentication rather than silently trusting a session obtained this way, (b) checking whether GoTrue's PKCE flow can actually be enabled for `email_change` verification specifically (the SDK/GoTrue version in use may not support it for this verification type — worth confirming against the installed GoTrue version's real docs before assuming a flag flip fixes it), or (c) at minimum, treating this as a known-accepted risk and documenting it explicitly in `.agent/GAPS.md`/`SCOPE.md` rather than leaving the current (incorrect) comment in `profile.ts` implying it's already handled safely.

---

## 3. useCurrentUser — real sign-out/sign-in transition test

Wrote and ran a real Playwright test (deleted after use, not left in the repo): sign in as user A, set a distinguishing display name, confirm it renders; sign out; sign in as a completely different real user B; confirm **zero** trace of A's name/email anywhere in the DOM, and B's own (default, since B never set one) identity renders correctly and immediately.

**Passed cleanly.** `onAuthStateChange`-based subscription (not a one-time fetch) does what its own docstring claims — no stale-session leak across a real sign-out/sign-in sequence in the same tab.

---

## 4. Sign-out-other-sessions — real revocation test

Two independent real sessions for the same user (A = "current device," B = "other device"). Confirmed B's access token works before revocation. Called `signOut({scope: 'others'})` from session A (the exact call `SecuritySection.handleSignOutOthers()` makes).

- **B's old refresh token, used to request a fresh access token:** rejected — `Invalid Refresh Token: Refresh Token Not Found`. Real revocation, not a client-side-only "success" message.
- **A's own session:** confirmed still fully valid afterward — `scope: 'others'` does not touch the caller's own session.
- **Bonus check, not in the original brief:** even B's still-live, not-yet-expired *access token* was rejected on the very next API call — `Session from session_id claim in JWT does not exist`. This is a **stronger** guarantee than the UI copy states ("stop working the next time they'd need to refresh, not necessarily this instant") — the real behavior is full, immediate session invalidation, not just a refresh-token block. Low-severity, positive-direction inaccuracy in the UI copy (undersells the real guarantee); not a security bug.

---

## 5. Rerank cost-guard — real call-counting

Found this already implemented to the exact discipline requested, at `apps/api/tests/test_retriever.py`, via a `FakeReranker` spy that records every `rerank()` call:

- `test_rerank_is_opt_in_reranker_never_called_when_rerank_not_requested` — asserts `fake_reranker.calls == []` when `rerank` is omitted (defaults False).
- `test_rerank_true_uses_the_rerankers_result_ordering_when_it_succeeds` — asserts `len(fake_reranker.calls) == 1` when `rerank=True`.

Ran both directly just now: **both pass.** Composed with the already-existing `test_query.py` tests proving `QueryRequest.rerank` reaches `retriever.retrieve(rerank=...)` unchanged, this proves the full chain (HTTP request → route → retriever param → exact rerank call count) without needing a live, quota-costly Voyage-endpoint-level call count.

---

## 6. Non-streaming `askQuestion()` path — PRIORITY FINDING, audited for drift

Read `apps/api/routes/query.py` in full. `post_query` (non-streaming) and `post_query_stream`/`_stream_query_events` are in the **same file**, and the streaming function's own docstring states the intent explicitly: *"Deliberately mirrors post_query()'s logic step-for-step ... the one thing that must NEVER drift between the streaming and non-streaming paths is which citations get dropped/kept."*

Verified this claim rather than trusting it:

- **Citation recovery cascade** (`_extract_claim_spans`) — literally the same function, called identically by both routes.
- **`_is_resolvable_marker` defense-in-depth guard** — same function, same call sites in both.
- **UNSUPPORTED-drop / UNVERIFIED-keep logic** — identical `if verdict.verdict == VerdictLabel.UNSUPPORTED` check in both, byte-for-byte matching structure. `UNVERIFIED` itself is generated entirely inside `Verifier.verify_batch()` (confirmed in `services/verifier.py`), which **both** routes call — there is no path-specific code that could drift on this.
- **Rate limiting** — both routes carry `@limiter.shared_limit(..., scope=QUERY_RATE_LIMIT_SCOPE)` with the identical scope string, and a real, currently-passing test (`test_rate_limit.py`, "Acceptance criterion: /query and /query/stream share ONE combined per-user limit") proves this live, not just by code inspection.
- **Frontend rendering** — `askQuestion()` (non-streaming) and the streaming path's `onCitationsResolved` handler both call the **same** `buildAssistantMessage()` (`lib/chat/parse-message.ts`) with the same argument shape — citation verdict styling (including UNVERIFIED) cannot drift between paths because it's one shared function fed the same data shape either way.
- **Test coverage of `/query` specifically** (not just `/query/stream`) for these hardening behaviors: `grep`-counted 35 occurrences of `UNVERIFIED`/`claim_span`/recovery-cascade-related assertions in `test_query.py` — this is real, existing coverage of the non-streaming path for exactly the behaviors in question, not just structural code-sharing that happens to be untested.

**Verdict: no drift found.** The "dormant code path" framing in the task brief applies accurately to the **frontend** `askQuestion()` wrapper (unused by the chat UI until this batch wired it into the streaming-toggle-off branch) — it does not apply to the backend `/query` route itself, which has been continuously exercised by its own substantial test suite the whole time. This is a case where the concern was reasonable to raise but the architecture (shared helper functions + shared Verifier + shared rate-limit scope, by design, not by accident) already prevents the failure mode described.

---

## 7. localStorage usage

Grepped every `.agent/*.md` doc (`STANDARDS.md`, `ARCHITECTURE.md`, `SCOPE.md`, `FEATURES.md`, `GAPS.md`, `MEMORY.md`, `SCHEMA.md`, `API_CONTRACT.md`) for `localStorage`/`window`/"client-side storage" — **zero matches of any prohibition**. Also checked for a technical enforcement (eslint `no-restricted-globals`/`no-restricted-syntax`) — none exists. The implementation's own claim ("no project rule forbids real localStorage... checked STANDARDS.md and every other `.agent/*.md` doc") is **confirmed accurate**.

**Hydration-mismatch check:** read `hooks/use-preferences.ts` directly. `usePreferences()`'s `useState` initializes to `DEFAULT_QUERY_PREFERENCES` unconditionally (same value server-side and on the client's first render — no `localStorage`/`window` access during render), then reads the real stored value inside a `useEffect` (client-only, post-mount). This is the textbook SSR-safe pattern: React's hydration check only compares the *first* client render against the server-rendered HTML, and since both use the same default, there is no mismatch to warn about. This differs from theme's pattern (which needs a *pre-hydration* injected script to avoid a *visual* flash) — correctly, since the code comment explains nothing here needs to avoid a visual flash the way theme does.

One minor, low-severity note not called out in the implementation's own report: the Settings page's own rerank/streaming checkboxes **do** briefly render their default state and then flip to the real stored value after mount — a visible flash, just confined to the Settings page itself rather than the whole app's background color the way theme's flash would have been. This is not a hydration *error* (no console warning, no correctness bug), just a minor, low-stakes UX quirk worth being aware of if this is ever revisited.

---

## 8. Credential hygiene — fabricated key exposure

The implementation reported briefly writing a fabricated-looking Supabase anon key to `apps/web/.env.local` before self-catching and correcting it. Checked what's available in this environment:

- **Git:** `apps/web/.env.local` is covered by `apps/web/.gitignore`'s `.env*.local` pattern (confirmed via `git check-ignore -v`). `git status --short` on the file returns nothing (not tracked, nothing staged). `git log --all --full-history -- apps/web/.env.local` returns no history — the file has **never** been committed, at any point, in this repo.
- **Repo-wide grep** for the fabricated value's distinctive signature substring (`PJjJoOgnwLDLPvGz0aVMHRPnDqEbHrJfkTLGqvpz-QQ`) and its embedded `iat` timestamp — **zero matches anywhere in the working tree.**
- **Scratchpad/temp/test-results directories** — same grep, **zero matches.**
- **Live process check:** confirmed via `Get-CimInstance Win32_Process` that no `next dev`/`pnpm dev` process was running at the time of either edit — Next.js dev-mode env hot-reload (which could otherwise have loaded the fabricated value into a running server's memory / baked it into served client JS, since it's a `NEXT_PUBLIC_` variable) had no running process to reload into. Playwright's own `webServer` (which does start a `pnpm dev` instance) is torn down when its `npx playwright test` invocation exits, and no playwright run occurred in the window between the fabricated edit and its correction.

**What I can confirm:** the fabricated value never reached git, never appeared in any file under this repo tree (including test artifacts/logs), and no live process was running that could have loaded it.
**What I cannot fully rule out**, for full honesty: I have no way to inspect OS-level file-system event logs, editor undo-history caches, or any process outside what `Get-CimInstance`/repo grep can see — so this is "everything checkable within this environment came back clean," not an absolute forensic guarantee. Given the value in question was an anon key (a publishable, non-secret-by-design credential even in the worst case), the practical severity of any theoretical residual exposure is low regardless.

---

## 9. Regression — fresh run

**Backend (`apps/api`), full suite, freshly run just now:** `362 passed, 18 skipped` — clean, including the previously-flaky `test_mixed_provider_scope_a_query_only_matching_the_gemini_chunk_still_surfaces_it`, which passed this run.

**E2E suites, freshly run just now (not relying on any prior session's run):**
- `settings.e2e.ts` (batch 1) — **7/7 passed**, including its own independent avatar-RLS, sign-out-other-sessions, and email-double-confirm tests (which corroborate items 1, 2, and 4 above at a lighter-weight level).
- `auth.e2e.ts` — **7/7 passed.**
- `upload.e2e.ts` — **6/6 passed.**
- `preferences.e2e.ts` (batch 2) — **10/12 passed.** The 2 failures (`streaming preference off...`, `rerank on vs off...`) are **not code regressions** — confirmed via the backend log both are real `429 RESOURCE_EXHAUSTED` responses from Gemini's `gemini-3.6-flash` free-tier **daily** quota (20 requests/day), the same class of constraint `.agent/MEMORY.md`'s 2026-07-26 entry already documents for a different model. This is a cumulative-across-the-day exhaustion from this session's own repeated real-API test runs (both the implementation's original verification pass and this audit's own live testing), not something a code fix addresses. Unlike Voyage's per-minute limit, this does not recover within the session — only a new day (Pacific time) or a paid tier clears it.
- `chat.e2e.ts` and `conversation-management.e2e.ts` — **not run this pass.** Both make real `/query`/`/query/stream` calls and would almost certainly hit the same exhausted daily Gemini quota just described, producing failures indistinguishable from the ones just seen — running them now would burn further real API calls for no diagnostic value. Recommend re-running these specifically once the daily quota resets, rather than treating today's non-run as "unverified forever."

**Cleanup performed:** backend `uvicorn` process stopped; `apps/web/.env.local` reverted to the real hosted Supabase project (`https://nbrfjbjjjhawscncshdz.supabase.co`), reusing and re-verifying the same real anon key. No temporary test files were left in the repo (the one written for item 3 was deleted after use); `git status` confirms no unintended changes beyond the pre-existing uncommitted Settings batch 1/2 work.
