# Memory

Persistent agentic memory. Never cleared. Append-only within sections. Tag every entry with timestamp + agent ID.

**Read at session start:**
1. `§Anti-patterns` — what not to repeat
2. `§Open questions` — what still needs human input

**Updated at session end** via `/memory-sync`.

---

## §Anti-patterns

Things tried and failed, or explicitly rejected during design. Do not retry without human confirmation.

### 2026-07-22 [claude-code] — CLIP-style dual-tower embeddings
**Context:** During v1 strategy selection, considered CLIP-style multimodal embeddings (separate text and image encoders).
**Why rejected:** Modality gap — image embeddings cluster near other image embeddings, text near text. Cross-modal retrieval (text query → image match) is degraded. Voyage's unified single-encoder architecture eliminates this. Do not swap to CLIP-family without solving the modality gap first.

### 2026-07-22 [claude-code] — Vercel serverless for the Docling backend
**Context:** User asked whether Python backend could run as Vercel serverless functions.
**Why rejected:** (1) Vercel serverless function size cap of 250MB is smaller than PyTorch + Docling. (2) 10-30s cold starts on first invocation kill UX. (3) Default 15s timeout too short for multi-page parse. Do not attempt to move FastAPI to Vercel serverless. If Render becomes a problem, use Railway or Fly.io — not Vercel.

### 2026-07-22 [claude-code] — Application-level tenant filtering as primary defense
**Context:** Considered relying on app-level `WHERE user_id = ?` alone for multi-tenancy.
**Why rejected:** Every route becomes a potential leak surface. RLS at Postgres is the primary defense. App-level user_id filters remain for clarity/testability but the DB is the enforcement point. Never bypass RLS with service-role for a user-facing read path.

### 2026-07-22 [claude-code] — Building all four multimodal-RAG strategies simultaneously
**Context:** User initially proposed offering all four strategies (extract-to-text, unified multimodal embeddings, layout-aware, page-as-image) as user-selectable in v1.
**Why rejected:** Massive engineering surface for a solo portfolio project. Ship layout-aware first end-to-end, add other strategies behind a common interface in v2 once the shape is proven. Do not build the strategy-selector abstraction before v1 ships.

### 2026-07-22 [claude-code] — Building over-engineered documentation system
**Context:** Considered separate DECISIONS.md, API_CONTRACT.md, CONVENTIONS.md, flow.md, PROJECT_STATE.md files.
**Why rejected:** Seven living docs is more overhead than a solo dev needs. Merged into agent-os defaults (CHANGELOG absorbs decisions, ARCHITECTURE absorbs conventions, HANDOFF absorbs flow narrative). Only added .agent/SCHEMA.md and .agent/API_CONTRACT.md as project-specific additions on top of agent-os defaults. Do not add more living docs without a concrete failure case that the existing set doesn't cover.

### 2026-07-22 [claude-code] — Validating a security mechanism using a token forged by that same mechanism
**Context:** FEAT-003 (JWT auth middleware) was first built and "verified live" by self-signing an HS256 token with `SUPABASE_JWT_SECRET` and confirming the middleware accepted it. It did — because the verification code and the test both used the identical secret and algorithm, so the check could only ever pass. It never touched a real Supabase-issued token. When challenged to test against an actual login-issued JWT, that token turned out to be ES256 (Supabase's asymmetric signing-keys scheme, verified via JWKS) — completely incompatible with the HS256-shared-secret implementation, which had been sitting in `main` as "complete" and committed (`f5cd00f`).
**Why rejected:** Never treat "I forged a credential and my own code accepted it" as verification of anything except that the forging and the checking agree with each other — that's circular by construction. Never validate a security mechanism using a token forged by that same mechanism; get a token from the actual issuing system (a real login, a real signup, a real third-party auth flow) before calling any auth code "verified." This generalizes beyond JWTs — the same trap applies to webhook signature verification, API key checks, or any place where "I can produce output my own code accepts" is being mistaken for "my code correctly implements someone else's spec."

### 2026-07-22 [claude-code] — Docling default pipeline options
**Context:** FEAT-004 (Docling parser service). Docling's default pipeline downloads RapidOCR models on first use even when parsing fully-digital PDFs — must explicitly set `do_ocr=False` to stay in scope and avoid the unexpected download. Separately, `generate_picture_images` defaults to a setting that silently makes every figure element's image `None` with no error — must explicitly set `generate_picture_images=True` or figure extraction appears to work (elements exist) while actually producing no usable image data.
**Why rejected:** Both are silent-failure-shaped defaults, not the kind of bug tests catch on their own — "elements exist" or "no error raised" looks like success. Always construct `DocumentConverter` with explicit `PdfPipelineOptions(do_ocr=False, generate_picture_images=True)` (see `apps/api/services/parser.py`) rather than relying on defaults, and re-check both flags if upgrading Docling in case defaults change.

### 2026-07-23 [claude-code] — Docling prints its own traceback to stderr on conversion failure
**Context:** FEAT-004 Codex review. When `converter.convert()` fails (e.g. against a truncated/corrupt PDF), Docling/pypdfium2 print their own internal traceback to stderr *before* `Parser.parse()`'s `except Exception` catches it and re-raises as `ParseError`. Confirmed: the error is not swallowed — the caller still receives a clean `ParseError`, and `ParseError.page_number` is correctly `None` in this case since conversion failed before any page provenance existed. The stderr output is purely cosmetic noise from Docling's own internals, not a sign our error handling is leaking or failing.
**Why rejected (verdict: not a bug, do not re-investigate):** This is expected third-party library behavior we don't control and don't need to suppress — `Parser.parse()`'s contract (raise `ParseError`, nothing else) already holds regardless of what gets printed to stderr. If this comes up again (e.g. noisy test output, a log-scraping alert triggering on stderr content), the fix is to filter/suppress at the logging or CI layer, not to treat it as a parser defect.

### 2026-07-23 [claude-code] — Chunker's Tier-2 caption matching is greedy, not globally optimal
**Context:** FEAT-005 (chunker service). `_resolve_tier2_captions()` processes captions in document order; a table/figure claimed via `claimed_targets` is unavailable to every caption processed after it. If two orphaned captions could both plausibly match the same single unclaimed table, whichever caption is encountered first in the document wins it — the second is left unmatched (a standalone `"unmatched"` chunk) even if, with full knowledge of both captions, it would have been the better fit for that table and the first caption would have been better left unmatched (or matched to a different table it also has a claim on).
**Why rejected (verdict: acceptable simplification, not a bug — revisit only with real evidence):** A globally optimal assignment (e.g. bipartite matching over all orphaned captions vs. all unclaimed targets on a page) is meaningfully more complex for a scenario that's rare in practice — most pages have at most one orphaned caption near at most one uncaptioned table. Measured on `table_heavy.pdf` (the only real multi-caption-per-page data available so far): 0 instances of two orphaned captions competing for one target. Do not build the globally-optimal version speculatively. Revisit only if a real document is found where greedy-first-wins produces a visibly wrong pairing (verified `test_tier2_first_caption_wins_when_two_captions_compete_for_one_target` documents and locks in today's greedy behavior as a regression test, not an endorsement that it's the best possible answer).

### 2026-07-23 [claude-code] — "No current caller violates an invariant" is not "the invariant is enforced"
**Context:** FEAT-007 Codex review of `routes/ingest.py`. `run_ingest_pipeline()`'s correctness depends on `storage_path` actually belonging to `user_id` — but that was only ever checked by `post_ingest()`, its one caller, before scheduling the background task. The pipeline function itself trusted the caller unconditionally. Nothing was *currently* broken — the only caller in the codebase always checked first — but that's a fact about today's call graph, not a property of the function. The same review also caught two adjacent instances of the identical shape: dependency construction (`get_service_role_client()`, `Parser()`, `Chunker()`, `Embedder()`) happened before the function's own `try` block, so a construction failure had no handler to reach — again, "works today" because construction happens to succeed today, not because failure was handled. Fixed by moving the invariant check and dependency construction inside `run_ingest_pipeline()`'s own `try`, with a dedicated `test_run_ingest_pipeline_refuses_mismatched_user_id_and_storage_path` that calls the function directly, bypassing the route entirely, to prove the function — not its caller — enforces the precondition.
**Why rejected (verdict: real gap, generalizes beyond this instance):** This project has now found this exact shape of gap more than once (see FEAT-003's circular-JWT-verification entry above — a different flavor of "the check technically passed" masking "the check doesn't actually prove what it claims to"). The general lesson: when reviewing or writing a function whose correctness depends on a precondition, checking "does every current caller uphold it" is necessary but not sufficient — ask "does the function itself refuse to proceed if it doesn't hold," independent of who's calling it or how many callers exist today. This matters most for background/async entry points and anything with more than one caller, where the "just check at the call site" instinct is easiest to reach for and easiest for a future second caller to silently violate. Apply this lens proactively in future reviews, not just when Codex happens to flag it.

### 2026-07-23 [claude-code] — `startswith()` is not a safe authorization boundary for path-like input
**Context:** A self-audit (run proactively, ahead of a separate Codex security/perf review) found `routes/ingest.py`'s `storage_path` ownership check — `storage_path.startswith(f"uploads/{user_id}/")` — was a genuine, confirmed-exploitable path traversal, not a theoretical concern. `"uploads/{attacker}/../{victim}/file.pdf"` passes that check (a literal string comparison has no concept of ".." semantics), and Supabase Storage's own server resolves the ".." before fetching the object — proven via both the storage3 SDK and raw HTTP directly against the real local Storage stack, returning the victim's actual file content through the app's privileged service-role client (which bypasses RLS entirely). The first fix attempt — reject any raw ".." path segment, require `posixpath.normpath(path) == path` — looked reasonable and matches the standard advice for this class of bug, but was **also** proven exploitable by the same live-testing discipline: a percent-encoded variant, `"uploads/{attacker}/%2e%2e%2f{victim}/file.pdf"`, contains no literal ".." segment and is already normpath-canonical (`posixpath` doesn't decode URL encoding), yet still resolved to the victim's file — some layer in the request chain (most likely the Storage server itself decoding the request URI's path component, which is standard, spec-compliant HTTP behavior per RFC 3986's definition of `%2e%2e` as equivalent to `..`) reversed the encoding before our check ever saw it. The fix that actually held up under live re-testing: stop trying to blacklist spellings of ".." (literal, encoded, double-encoded, and whatever comes next) and instead whitelist the allowed character set for the untrusted portion of the path (`^[A-Za-z0-9._-]+$`, no slash — real or encoded — permitted at all), checked against a prefix built entirely from trusted, server-side data (the JWT's own `user_id`, never client input).
**Why rejected (verdict: real, distinct lesson from the two "invariant not enforced" findings above):** Those findings were about *whether* a check runs at all (a check existed and was correct, just only in one of two places it needed to be). This is different in kind: the check ran in the right place every time, and was still wrong, because a bare prefix/substring comparison on a path-like string is not a security boundary — path resolution happens in a different, later layer (the filesystem, an HTTP server's URI decoding, a storage backend) that a purely textual check cannot see or agree with in advance. General lesson: for any input that will later be interpreted as a path (or URL, or any other string with its own resolution/decoding semantics), validate with a **whitelist of what's structurally impossible to misuse**, not a blacklist of known-dangerous patterns — and prove the fix against the real downstream resolver empirically (as done here, twice, since the first "obviously correct" fix wasn't), not by inspection of the validation code alone.

### 2026-07-26 [claude-code] — `gemini-2.5-flash`'s free tier is capped at 20 requests/DAY, per-model, NOT shared with the generation/verification models
**Context:** FEAT-017 (OCR fallback) added the first real use of `gemini-2.5-flash`. A full backend suite run late in the same session hit a real `429 RESOURCE_EXHAUSTED`. Confirmed real, not a fluke — the identical test had passed earlier the same session with genuine recovered OCR text; only cumulative same-day call volume (repeated `test_parser.py` runs during development, each making 2 real calls against `scanned.pdf`) exhausted it.
**Exact evidence:** `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`, `quotaDimensions: {location: global, model: gemini-2.5-flash}`.
**Scope, confirmed not assumed (2026-07-26 follow-up check):** fetched Google's live rate-limits/pricing/model docs directly — none publish exact per-model RPD numbers for `gemini-3.6-flash`/`gemini-3.5-flash-lite` (Google gates the actual dashboard numbers behind an authenticated AI Studio page this environment can't reach). What IS confirmed, from the quota metric's own name and dimensions plus matching community reports of the identical `quotaId`: this cap is **per-model**, not a pool shared across every Gemini model in the project — the metric is literally named "PerProjectPerModel" and the real error's own `quotaDimensions` scopes it to `model: gemini-2.5-flash` specifically. `gemini-3.6-flash` (generation) and `gemini-3.5-flash-lite` (verification) are a different model family (Gemini 3.x vs 2.5) with their own separate quota bucket — today's OCR exhaustion did not and could not affect them. Their own free-tier RPD numbers are still unconfirmed (not published anywhere fetchable) — don't assume they're higher just because they're newer, verify separately before relying on it if generation/verification testing volume ever becomes a concern.
**Why this matters:** Unlike Voyage's rate limit (RPM — recovers within the same session if you wait/back off), this is a **daily** cap. It will not recover within a session no matter how long you wait or how you pace calls — only a new day (Pacific time reset) or a paid tier clears it. Any test or workflow making repeated real `gemini-2.5-flash` calls should expect to exhaust this fast during active development and budget for it: prefer a `FakeOcrClient` for anything not specifically testing real OCR recovery (already the pattern `test_parser.py` uses), and treat a single real-OCR test failing with `RESOURCE_EXHAUSTED` as this cap, not a regression — check the error message's `quotaId`/`quotaDimensions` before assuming the code broke, and check which model they name before assuming it spilled over into a different feature's quota.

### 2026-07-27 [claude-code] — A synchronous call inside an `async def` SSE generator silently defeats streaming, and neither raw httpx nor FastAPI's `TestClient` reliably catch it
**Context:** FEAT-016 (`POST /query/stream`). `_stream_query_events()` yields an SSE `verifying` event, then called `Verifier.verify_batch()` — synchronous, blocking on `ThreadPoolExecutor.result()` — directly, with no `await`. This froze the asyncio event loop for the call's entire real duration (~8s, real Gemini verification calls). The observable symptom: the client received the `verifying` event and the following `citations-resolved` event at the **identical timestamp**, instead of with the real multi-second gap between them the whole point of a separate event was to surface. Two layers of tests were run against this and both passed anyway: a raw `httpx.Client.stream()` script, and pytest against FastAPI's `TestClient` (also httpx-based) — in both cases the client's own read/buffering behavior happened to coalesce the frames the same way the bug did, so nothing looked wrong. Only a real Playwright browser session against the real running dev server (`next dev` + `uvicorn`, no test harness in between) — sampling the DOM over time and, once that was ambiguous, adding a temporary client-side `console.log(performance.now())` per SSE event — made the simultaneous-arrival timestamps directly observable.
**Why this matters (verdict: real, generalizes beyond this one call site):** An `async def` generator that calls blocking code without `await`ing it in a thread doesn't just "run slower" — it can silently prevent the ASGI server from flushing bytes it already handed to `send()` moments earlier, because uvicorn needs the event loop to actually get scheduled to do that flush, and a long synchronous call inside the same coroutine starves it. This is invisible to anything that measures *content correctness* (the final answer/citations were always right) or even *total latency* (the numbers looked plausible) — it only shows up as a *timing/ordering* defect between two events that are supposed to be temporally separate, which is exactly the shape neither a unit test asserting on parsed event data nor a manual latency stopwatch is built to catch. httpx-based clients (raw or via `TestClient`) are not a reliable oracle for this class of bug — their own internal buffering can mask the exact symptom a real browser exposes. **Fix pattern:** wrap every synchronous/blocking call inside an async SSE (or any async streaming) generator in `asyncio.to_thread(...)`, not just the "slow" one — retrieval, verification, DB persistence, signed-URL generation were all wrapped in `routes/query.py`'s `_stream_query_events()`, not only `verify_batch()`. **When writing or reviewing any new async streaming endpoint, verify inter-event timing with a real browser (or at minimum a raw socket read with per-byte/per-chunk timestamps), not just a test-client-parsed event sequence** — the sequence being *correct* does not prove the *timing* between events is real.

---

## §Open questions

Things that need human input before proceeding. Do not assume answers.

### 2026-07-22 [claude-code] — Chunking granularity
**Context:** ~500-token target chosen. Could be smaller (finer citations) or larger (more context per chunk).
**Leaning:** Start at 500 tokens with element-boundary respect. Revisit after real documents are ingested.
**Blocking:** Not blocking; FEAT-005 encodes 500-token default.

### 2026-07-22 [claude-code] — Document status update mechanism
**Context:** Polling vs Supabase Realtime subscription for parsing progress.
**Leaning:** Polling (simpler, works everywhere, no websocket setup).
**Blocking:** Not blocking; FEAT-014 defaults to polling.

### 2026-07-24 [claude-code] — Google OAuth sign-in completion has never been verified end-to-end
**Context:** FEAT-013 self-audit found the OAuth callback was entirely missing (fixed same day —
`app/auth/callback/route.ts`, shares its PKCE code-exchange mechanism with password recovery,
which IS fully live-verified end-to-end). Completing an actual Google sign-in could not be tested
from this environment for two independent reasons: the local Supabase project has no
`[auth.external.google]` client configured (`.../authorize?provider=google` returns `"Unsupported
provider"` before ever reaching Google), and even with one configured, completing Google's real
consent screen requires a real Google account and can't be automated by an agent regardless
(Google blocks headless/scripted logins). See `.agent/GAPS.md`'s matching entry for the full
mechanism proof and the manual verification checklist to run once a real client exists.
**Leaning:** The shared code-exchange mechanism is proven; only Google's own consent screen and
the resulting real session are unverified. Low risk, but not zero — do not assume it works.
**Blocking:** Blocking for shipping OAuth specifically (not for the rest of FEAT-013). Needs a
real Google OAuth client (likely at deploy time) and one manual click-through before considering
OAuth sign-in done, not just "the code looks right."

---

## §Decision log

Every fork, what was chosen, why. Append-only.

### 2026-07-22 [claude-code] — v1 strategy: layout-aware parsing (Docling)
**Alternatives considered:** Extract-to-text (simpler but lossy), unified multimodal embeddings (heavier compute, less deterministic chunks), page-as-image ColPali-style (highest fidelity but heaviest compute per page).
**Chosen:** Layout-aware parsing via Docling.
**Reasoning:** Widest applicability across document types, best portfolio depth, fully free at portfolio scale, preserves table structure and element relationships better than plain text extraction.

### 2026-07-22 [claude-code] — Embeddings: Voyage multimodal-3.5
**Alternatives considered:** OpenAI text-embedding-3, Cohere embed-4, Jina v4, self-hosted nomic-embed.
**Chosen:** Voyage multimodal-3.5.
**Reasoning:** Unified encoder avoids CLIP modality gap on cross-modal retrieval. 200M tokens + 150B pixels free tier covers portfolio scale by wide margin. Anthropic-recommended, natural fit with Claude generation.

### 2026-07-22 [claude-code] — Storage: Supabase (Postgres + pgvector + Storage + Auth)
**Alternatives considered:** Dedicated vector DB (Pinecone, Weaviate, Qdrant), separate auth provider (Clerk, Auth0), separate file storage (S3).
**Chosen:** Supabase for all four.
**Reasoning:** One system to run and deploy. pgvector is production-ready for portfolio-scale (<10M chunks). RLS enables true multi-tenancy without app-level enforcement. Free tier is workable.

### 2026-07-22 [claude-code] — Multi-tenancy day 1 via RLS
**Alternatives considered:** Ship single-user v1, retrofit multi-tenancy later.
**Chosen:** Multi-tenant schema from day 1, tested single-user until Phase 3.
**Reasoning:** Retrofitting is painful (touches every query). RLS at schema is a one-time cost that closes an entire class of bugs permanently.

### 2026-07-27 [claude-code] — Reranking: opt-in (`rerank=True`), not default-on (resolves the 2026-07-22 open question)
**Alternatives considered:** Always-on reranking for every `retrieve()` call; opt-in caller-toggled parameter.
**Chosen:** Opt-in — `retrieve(..., rerank: bool = False)`, Voyage `rerank-2.5`, default off.
**Reasoning:** The original open question's leaning ("measure quality without it first") was followed: FEAT-009's real quality fixture already found RRF alone lands the expected chunk in the top-5 4/4 times. Re-running the exact same 4 questions with reranking enabled found real added latency (mean 380.8ms) but zero rank improvement in that run (baseline already sat at rank 1 for all 4 — a ceiling this particular run couldn't test past) and, importantly, zero regression either. Given real cost with no demonstrated benefit yet, opt-in stands; revisit if/when a caller with a harder real question set shows RRF alone falling short. Full write-up: `.agent/FEATURES.md`'s FEAT-009 entry, CHANGELOG.md 2026-07-27.

### 2026-07-22 [claude-code] — Repo shape: monorepo
**Alternatives considered:** Two separate repos (apps/web, apps/api).
**Chosen:** Monorepo with `apps/web`, `apps/api`, `docs/`, `.agent/`.
**Reasoning:** Solo dev + multi-agent, agents share context better with one repo. Shared TS types easier to sync. Simpler deploys (Vercel and Render both handle subdirectories).

### 2026-07-22 [claude-code] — Deploy: Vercel (web) + Render (api)
**Alternatives considered:** All-Vercel (Python serverless), Railway, Fly.io, self-hosted VPS.
**Chosen:** Vercel + Render.
**Reasoning:** Vercel serverless can't fit Docling under size limits. Render free tier is sufficient (750 hrs/mo, Docker for Python). Railway is the fallback if Render's 15-min idle spin-down becomes a UX issue.

### 2026-07-22 [claude-code] — Four-agent development workflow (user override)
**Alternatives considered:** Recommended reducing to two agents (claude-code + claude-design) for solo scope.
**Chosen (by user):** Keep all four (claude-code, claude-design, gemini, codex).
**Reasoning:** User wants full multi-agent orchestration experience for portfolio angle. Handoff discipline via AGENT.md role table + commit tags mitigates coordination overhead. Reversible if friction exceeds value by Week 2.

### 2026-07-22 [claude-code] — Documentation set: agent-os defaults + SCHEMA + API_CONTRACT
**Alternatives considered:** Full seven-doc system (adding DECISIONS, CONVENTIONS, flow, PROJECT_STATE).
**Chosen:** Agent-os defaults (AGENT, CHANGELOG, SCOPE, ARCHITECTURE, STANDARDS, FEATURES, MEMORY) + two additions (SCHEMA, API_CONTRACT).
**Reasoning:** Agent-os covers most needs. SCHEMA is essential for a DB-heavy project. API_CONTRACT documents internal endpoints; external APIs handled by `/api-check` under `.agent/api-docs/`. Nine docs is at the edge of manageable; do not grow this further without concrete evidence a doc is missing.

### 2026-07-22 [claude-code] — Project name: Docify
**Alternatives considered:** Placeholder `multimodal-rag` (working name only, never intended as final).
**Chosen:** Docify.
**Reasoning:** User locked the name, closing the open question that was blocking FEAT-023 landing page work. Find-replaced across AGENT.md (`name:` field + Open→Locked decisions), .agent/API_CONTRACT.md (placeholder Render URL), and this entry's own move out of §Open questions. No occurrences existed in .agent/ARCHITECTURE.md or .agent/FEATURES.md — checked, nothing to change there.

### 2026-07-22 [claude-code] — Generation + verification provider: Gemini (reversing Claude)
**Alternatives considered:** Staying on Claude paid tier post-credit-expiry (the original plan reflected in the now-closed §Open questions entry); Gemini swap.
**Chosen:** Gemini — `gemini-3.6-flash` for generation, `gemini-3.5-flash-lite` for citation verification.
**Reasoning:** Consolidating all LLM calls onto a single provider. Gemini was already in the stack for OCR fallback (`gemini-2.5-flash`), so this removes the Anthropic SDK dependency and the second API credential entirely rather than running two providers side by side. Verified current model IDs via `/api-check gemini` on 2026-07-22 (cached in `.agent/api-docs/gemini.md`). Voyage (embeddings) is unaffected — different provider, different decision.
**Changed:** `AGENT.md`, `.agent/ARCHITECTURE.md`, `.agent/API_CONTRACT.md`, `.agent/FEATURES.md`, `.agent/MEMORY.md` (this entry), `.agent/api-docs/gemini.md` (new).

### 2026-07-24 [claude-code] — Agent rotation: gemini dropped as of FEAT-013
**Alternatives considered:** Keeping the four-agent rotation from the 2026-07-22 user override.
**Chosen:** Two agents — claude-code, claude-design. gemini's row removed from AGENT.md's §AGENT ROLES table; FEAT-013's owner changed from `claude-design + gemini (auth wiring)` to `claude-design (shell/UI) + claude-code (auth wiring)`.
**Reasoning:** Simplified to two agents based on how the project actually developed — claude-code absorbed everything gemini was scoped to own (frontend↔backend wiring, auth flows, integration glue).
**Changed:** `AGENT.md` (§AGENT ROLES table), `.agent/FEATURES.md` (FEAT-013 owner), `.agent/MEMORY.md` (this entry).

---

## §Assumptions

Explicit assumptions made without confirmation. Flag before acting on them.

### 2026-07-22 [claude-code] — Frontend stack
**Assumed:** Next.js 14 App Router, TypeScript, Tailwind, shadcn/ui.
**Rationale:** Matches Reminisce muscle memory per user context. Standard portfolio stack.
**Risk:** Low. User would flag differently.

### 2026-07-22 [claude-code] — Python version
**Assumed:** 3.12.
**Rationale:** Current stable, Docling supports it.
**Risk:** Low. Change is trivial if 3.11 preferred.

### 2026-07-22 [claude-code] — Package managers
**Assumed:** pnpm (web), uv (api).
**Rationale:** Faster, modern, standard for new projects.
**Risk:** Low. Swap to npm/pip if user prefers.

---

## §Agent identity log

Who wrote what, when. Populated as agents work.

*(empty — populated during real work)*
