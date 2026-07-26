# Features

Canonical list of every feature — status, phase, files, tests. This is the **no-feature-loss registry**.

**Status values:** `planned` · `in-progress` · `complete` · `tested` · `locked`

Rule: a feature is not `complete` until acceptance criteria pass AND `/gap-check` reports no gaps AND `/feature-check` reports test coverage.

---

## Phase 0 — Setup

### [FEAT-000] Repo skeleton
**Phase:** 0
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/web/package.json` — Next.js 14 App Router boilerplate
- `apps/api/pyproject.toml` — FastAPI + Docling + Voyage + Gemini deps
- `.gitignore` — node_modules, .env, __pycache__, HANDOFF.md, .agent/logs, .agent/index.json
- `README.md` — root readme with quick-start
- `apps/web/.env.example`, `apps/api/.env.example`

**Tests:** — (no test suite yet — nothing to test in a skeleton with no logic)
**Acceptance criteria:**
- [x] `pnpm install` succeeds in `apps/web`
- [x] `uv sync` succeeds in `apps/api`
- [x] `pnpm dev` starts Next.js on :3000
- [x] `uvicorn apps.api.main:app --reload` starts FastAPI on :8000

**Run:**
- Web: `cd apps/web && pnpm dev`
- API: `cd apps/api && uv run uvicorn main:app --reload`

**Changelog:** See CHANGELOG.md 2026-07-22 "feature: repo skeleton scaffolded (FEAT-000)"

---

### [FEAT-001] Supabase project + initial migration
**Phase:** 0
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/migrations/20260722_001_initial.sql` — schema per SCHEMA.md
- `apps/api/migrations/verify_20260722_001.sql` — dashboard-runnable verification query (checklist-style output)
- `apps/api/db/client.py` — service-role client
**Tests:**
- `apps/api/tests/test_migrations.py` — verifies tables exist, RLS is enabled, policies present (requires `TEST_DATABASE_URL`, skips if unreachable — not run against the live project this round; verification was via `verify_20260722_001.sql` in the dashboard instead)
**Acceptance criteria:**
- [x] Migration applies cleanly — applied by owner via Supabase dashboard SQL editor on the live project
- [x] All tables from SCHEMA.md exist with correct columns and types — confirmed via `verify_20260722_001.sql`, all checks `OK`
- [x] RLS is enabled on documents, chunks, conversations, messages, citations — confirmed via `verify_20260722_001.sql`, all checks `OK`
- [x] pgvector extension enabled, HNSW index on chunks.embedding — confirmed via `verify_20260722_001.sql`, all checks `OK`
- [x] Two storage buckets created: `uploads` (private), `figures` (private) with policies — confirmed via `verify_20260722_001.sql`, all checks `OK`

**Run:**
- Apply: paste `apps/api/migrations/20260722_001_initial.sql` into the Supabase dashboard SQL editor and run it
- Verify: paste `apps/api/migrations/verify_20260722_001.sql` into the SQL editor — every row should read `status = OK`
- (Alt/local path, not used this round: `supabase start` then `supabase db push`, `psql -h localhost -p 54322 -U postgres -d postgres -c "\dt"`)

**Changelog:** See CHANGELOG.md 2026-07-22 "feature: Supabase initial migration applied + verified (FEAT-001)"

---

## Phase 1 — Ingestion Pipeline

### [FEAT-002] `/health` endpoint
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/routes/health.py`
- `apps/api/models/health.py` — `HealthResponse` Pydantic model
- `apps/api/main.py` — wired `health.router` (not listed originally, but required for the route to be reachable)
**Tests:**
- `apps/api/tests/test_health.py`
**Acceptance criteria:**
- [x] GET /health returns `{status, version, timestamp}` with 200 — verified live: `{"status":"ok","version":"0.1.0","timestamp":"2026-07-22T14:12:40Z"}`
- [x] No auth required — no middleware exists yet (FEAT-003), confirmed 200 with no auth header

**Run:**
- `cd apps/api && uv run uvicorn main:app --reload` then `curl http://localhost:8000/health`
- Tests: `cd apps/api && uv run pytest tests/test_health.py -v`

**Changelog:** See CHANGELOG.md 2026-07-22 "feature: GET /health endpoint (FEAT-002)"

---

### [FEAT-003] JWT auth middleware
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/middleware/auth.py` — `JWTAuthMiddleware`, verifies ES256 via Supabase JWKS (`jwt.PyJWKClient`, injectable via constructor for tests, falls back to `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`)
- `apps/api/main.py` — wired middleware + `load_dotenv()`
- `apps/api/errors.py` — shared `error_envelope()` helper matching API_CONTRACT.md's error shape
**Tests:**
- `apps/api/tests/test_auth.py` — 8 tests against a locally-generated EC keypair + fake JWKS client: missing header, `/health` stays exempt, invalid signature, expired JWT, malformed JWT, valid JWT attaches `user_id`, JWKS URL derived from `SUPABASE_URL` (not hardcoded), missing-env-var fails loudly
**Acceptance criteria:**
- [x] Non-`/health` routes reject requests without `Authorization: Bearer <jwt>` with 401 — verified via pytest
- [x] Invalid signature returns 401 — verified via pytest
- [x] Valid JWT attaches `user_id` to `request.state.user_id` — verified via pytest (fake JWKS client); via my own admin-created test user (created, logged in, verified, deleted); **and independently by the owner**, against a user they created themselves via the Supabase dashboard — same temporary `/whoami` route, `request.state.user_id` matched their real `auth.users.id` (`abe7cc2e-a062-4058-8b00-16c2e022c8fe`) exactly. Route removed after (`main.py` back to zero diff).
- [x] JWT verification uses Supabase's JWKS (not a hardcoded value) — verified via pytest

**Note on how this became `complete`:** it was first marked complete on `f5cd00f` incorrectly — that verification was circular (an HS256 implementation checked against a self-forged HS256 token). Caught when challenged to verify against a real login; the real token was ES256/JWKS. Corrected implementation + tests, then verified three separate ways before marking complete again: fake-JWKS pytest suite, my own throwaway admin-created Supabase user, and the owner's own dashboard-created user via their own login. See CHANGELOG's correction entry and `.agent/MEMORY.md §Anti-patterns`.

**Run:**
- `curl -H "Authorization: Bearer <token>" http://localhost:8000/documents`
- Tests: `cd apps/api && uv run pytest tests/test_auth.py -v`

**Changelog:** See CHANGELOG.md 2026-07-22 "feature: JWT auth middleware (FEAT-003)" and the same-day correction entry "fix: JWT middleware verified against wrong signing scheme, corrected to JWKS/ES256"

---

### [FEAT-004] Docling parser service
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/services/parser.py` — `Parser`, `ParsedDocument` (`dropped_elements: int`), `ParsedElement` (now with `element_id`, `associated_caption_ids`, `association_method`), `BBox`, `ElementType`, `ParseError`
**Tests:**
- `apps/api/tests/test_parser.py` — 15 tests against three real fixture PDFs (`apps/api/tests/fixtures/`: `clean_digital.pdf`, `table_heavy.pdf`, `scanned.pdf`) plus fake-converter unit tests for iteration-failure and silent-drop paths
**Acceptance criteria:**
- [x] `Parser.parse(pdf_bytes) -> ParsedDocument` returns typed elements: text, heading, table, figure, caption, list — verified: no single fixture exercises all six (see element counts below), coverage confirmed across the three combined
- [x] Each element has: page_number, bbox (x0,y0,x1,y1), content, element_type — verified via pytest against every element in `clean_digital.pdf`
- [x] Tables are extracted as markdown-formatted content — verified: all 29 tables in `table_heavy.pdf` contain markdown pipe-table syntax
- [x] Figures are returned as PIL Image objects for downstream storage — verified against `scanned.pdf`'s one figure
- [x] Parse failures raise `ParseError` with the source page number — `ParseError.page_number` structurally tested; invalid/empty/truncated-bytes tests confirm the raise path; a fake-converter test confirms a failure *during element iteration* (not just at `converter.convert()`) also raises `ParseError`, carrying the last successfully-processed element's page number as "best available"

**Actual output per fixture (CPU, `do_ocr=False`, `generate_picture_images=True`) — pinned as a regression test, unchanged since first implementation:**
- `clean_digital.pdf`: 21 elements — 9 list, 6 heading, 5 text, 1 table. No figure/caption (fixture has neither). `dropped_elements`: 0.
- `table_heavy.pdf`: 66 elements — 29 table, 19 caption, 8 heading, 6 list, 4 text, across 11 pages. `dropped_elements`: 0.
- `scanned.pdf`: **2 elements total** across 3 pages — 1 heading (page 1's title, real embedded text, not OCR'd) + 1 tiny figure (logo). Pages 2–3: zero elements each. No crash — but see below. `dropped_elements`: 0.

**Scanned-PDF finding (informs FEAT-017 scoping):** with OCR off, a fully-scanned 3-page document degrades to almost nothing rather than erroring — "graceful" in that it doesn't crash, but it's silent, near-total data loss with no signal anything went wrong. FEAT-017 will need an explicit trigger heuristic (e.g. element count far below what page count would suggest) rather than relying on `ParseError` or empty output alone, since empty output here is indistinguishable from "this page is genuinely blank." (Full trigger-heuristic note now also in FEAT-017's own entry below.)

**Docling setup notes:** `do_ocr=False` set explicitly — Docling's default pipeline probes every page for OCR need and downloads RapidOCR models on first use even for fully-digital PDFs, which is both slow and out of FEAT-004's scope (OCR is FEAT-017). `generate_picture_images=True` set explicitly — `PictureItem.get_image()` returns `None` otherwise (`generate_picture_images` defaults to `False` in Docling). First run downloads the ~heron layout model from Hugging Face (one-time, cached after); full fixture suite now runs in ~4.5 min on CPU once models are cached (up from ~2.5 min — more tests re-parse the fixtures).

**Codex review follow-up #1 (2026-07-22):** found two real gaps, both closed. (1) The `try/except` around `converter.convert()` didn't cover the subsequent element-iteration loop — a failure during iteration, provenance access, or bbox extraction would have propagated unwrapped instead of as `ParseError`. Now the entire loop is wrapped, and the exception carries the last successfully-processed element's page number (`last_page_number`) as the best available context, since the failure itself may not have reached a new element's provenance yet. (2) Elements with missing provenance (a type we model, like caption, but with no page/bbox available) were silently `continue`d — no error, no log, invisible. Same silent drop existed for figures where `get_image()` returned `None`. Both now increment a new `ParsedDocument.dropped_elements` counter and log a WARN with what was dropped and why. Distinguished deliberately from labels we don't model at all (`page_header`, `footnote`, etc.) — that filtering stays silent since it's expected, not an anomaly; conflating the two would make `dropped_elements` noise rather than signal. Real-PDF corruption (truncation, mid-file byte removal) could not be made to reliably reproduce the iteration-level failure — pdfium either refuses to open the file outright (caught by the pre-existing `try/except`) or silently tolerates the corruption and returns fewer elements with no error. A fake converter/document is used instead for deterministic coverage of that specific path.

**Codex review follow-up #2 (2026-07-23) — table/figure ↔ caption association:** verified empirically (not assumed from docs) that the installed Docling version (2.87.0) exposes an explicit caption link: `TableItem.captions` / `PictureItem.captions` return a list of `RefItem`, each resolvable via `.resolve(doc)` to the actual caption `TextItem`, with a stable `self_ref` string (e.g. `#/tables/0`, `#/texts/1`) usable as a correlation id. **Tier 1 exists in this Docling version** — this was not a given going in. Added `element_id` (= Docling's own `self_ref`) to every `ParsedElement`; `associated_caption_ids: list[str]` populated on TABLE/FIGURE elements from resolved caption refs; `association_method: "explicit" | "none"` set on every CAPTION element (never left unset) — `"explicit"` if some table/figure's `captions` list claimed it, `"none"` otherwise. Resolution failures or refs pointing at something other than a caption are logged as WARN and skipped, not silently ignored. **Measured against `table_heavy.pdf`: 13 of 19 captions got `"explicit"`, 6 got `"none"`** (13 of 29 tables have at least one associated caption — the gap between 29 tables and 19 captions is multiple `table` elements sharing one caption, e.g. multi-year tables split into per-year sub-tables under one heading). Scope stops exactly at "extract what Docling knows" — proximity/positional matching for the 6 unclaimed captions (Tier 2) is explicitly FEAT-005's problem, not implemented here.

**All three fixtures re-verified to produce identical element counts (21/66/2, `dropped_elements: 0` for all three) after both follow-up rounds** — confirmed via a pinned-count regression test, not eyeballed.

**Run:**
- `cd apps/api && uv run pytest tests/test_parser.py -v` (first run downloads Docling's layout model from Hugging Face — expect several minutes; cached after)

**Changelog:** See CHANGELOG.md 2026-07-22 "feature: Docling parser service (FEAT-004)", 2026-07-22 "fix: parser exception coverage + silent-drop visibility (FEAT-004 Codex follow-up)", and 2026-07-23 "feature: table/figure caption association, Tier 1 (FEAT-004 Codex follow-up #2)"

---

### [FEAT-005] Chunker
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/services/chunker.py` — `Chunker`, `Chunk` (now with `split_from_element_id`), `TOKEN_BUDGET`, `MAX_CHUNK_TOKENS`
**Tests:**
- `apps/api/tests/test_chunker.py` — 21 tests: real fixtures (module-scoped parse) plus hand-built `ParsedDocument`s for shapes no real fixture has (captioned figure, orphaned caption, Tier-2 tie-breaking edge cases, oversized-element splitting, reparse stability)
**Acceptance criteria:**
- [x] Groups adjacent text/heading elements into chunks of ~500 tokens, respecting element boundaries (never splits a table row or a heading) — verified: `clean_digital.pdf`'s 19 groupable elements merge into one chunk (small enough to fit the token budget together), tables always get their own chunk regardless of size
- [x] Preserves metadata: page numbers, source element indices, element_type of primary element — verified against every chunk of `table_heavy.pdf`; `page_numbers` cross-checked against the actual pages of each chunk's source elements, not just type-checked
- [x] Each chunk has a stable `chunk_index` (ordinal within document) — verified sequential, 0-based
- [x] Figure elements produce their own chunks (one figure = one chunk, with caption prepended if adjacent) — verified against `scanned.pdf`'s uncaptioned figure (empty content, image present) and a hand-built captioned figure (no real fixture has one — neither `table_heavy.pdf` nor `scanned.pdf` contains a figure with a nearby caption)

**Caption association — Tier 1 (parser, explicit) + Tier 2 (chunker, heuristic), per task instructions:**
- Tier 1: a table/figure with `associated_caption_ids` already populated by the parser gets its caption(s) merged directly — no heuristic involved. 13/29 tables in `table_heavy.pdf`.
- Tier 2: a caption with `association_method == "none"` (parser found no explicit link) gets matched here by: same page → prefer adjacency in reading order → break ties by bbox distance. 6/19 captions in `table_heavy.pdf` needed this. **All 6 resolved to a plausible match on manual inspection** (caption text plausibly describes its matched table's actual content) — 0 left unmatched in this fixture. See CHANGELOG for the full pairing list.
- Every chunk carries `association_method: "explicit" | "heuristic" | "unmatched" | None` — traceable after the fact whether a caption pairing was Docling-verified, guessed, or not applicable. `merged_caption_ids: list[str]` records which caption(s), if any, fed into a chunk's content.
- Scope stops exactly where instructed: 10/29 tables in `table_heavy.pdf` have no caption at all (neither tier found one) — genuinely uncaptioned in the source, not a matching failure.

**Codex review follow-up (2026-07-23) — size ceiling + test-coverage gaps:** found one architecturally real gap (no size ceiling before chunks reach FEAT-006's embedding calls) and one test-coverage gap (Tier-2 tie-breaking logic was correct per manual probing but not permanently tested). Both closed. `MAX_CHUNK_TOKENS = 4000` added — verified against Voyage's real per-input limit (32,000 tokens, `.agent/api-docs/voyage.md`) rather than guessed; set at ~1/8 of it. Oversized tables split by row group (header/separator repeated on every part, never mid-row); oversized text/heading splits by paragraph then sentence boundary, never mid-sentence. Splitting happens strictly after caption association is resolved — a split part inherits its parent's `association_method` and `merged_caption_ids` unchanged, and gets a new `split_from_element_id` pointing back at the original element so the split is traceable. **Confirmed on all three fixtures: nothing actually splits** — the largest real table chunk measured ~418 proxy tokens against a 4,000 ceiling. Fixture counts and Tier-2 resolutions (13 explicit / 6 heuristic / 10 uncaptioned) unchanged. Splitting itself verified via synthetic oversized inputs (2,000-row table, 1,000-sentence paragraph) — both split correctly with no content lost or duplicated. Also converted Codex's manual synthetic probes into 6 new permanent tests (equidistant reading-order candidates, reading-order-beats-position, duplicate-claimant first-caption-wins, fully-equal-tie document-order fallback, plus splitting and reparse-stability). **Proxy token-count accuracy measured against Voyage's real local tokenizer** (`voyageai.Client.tokenizer(...)`, no API call needed): mean proxy/real ratio 0.94 across 46 real chunks, but individual chunks ranged 0.43x–2.31x — worst-case measured underestimate still leaves the 4,000-token ceiling at only ~29% of Voyage's real 32,000 limit even in that worst case. Conclusion recorded in `.agent/api-docs/voyage.md`: this ceiling has enough margin on its own: FEAT-006 doesn't need its own separate size defense based on what's been measured so far. `claimed_targets` first-caption-wins greedy limitation logged in `.agent/MEMORY.md §Anti-patterns` as an accepted simplification, not a bug.

**Run:**
- `cd apps/api && uv run pytest tests/test_chunker.py -v`

**Changelog:** See CHANGELOG.md 2026-07-23 "feature: chunker service, Tier 1 + Tier 2 caption association (FEAT-005)" and the same-day follow-up "fix: chunker size ceiling + Tier-2 test coverage (FEAT-005 Codex follow-up)"

---

### [FEAT-006] Voyage embedder wrapper
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/services/embedder.py` — `Embedder`, `EmbedError`, `Vector`
- `.agent/api-docs/voyage.md` (via `/api-check voyage` — expanded well beyond the FEAT-005-era token-limit-only version; now covers auth, request/response shape, retry behavior, and the corrected batch limit, verified against the installed SDK's source directly)
**Tests:**
- `apps/api/tests/test_embedder.py` — 23 tests: fake-client unit tests (fast, no network) including chunk<->vector correspondence and cardinality-mismatch tests, real-`voyageai.Client`-with-patched-transport tests for retry/error behavior (exercises the SDK's actual tenacity retry logic), one test against real chunker output on `table_heavy.pdf`, one test confirming Voyage's real tokenizer is available without an API call, and one real-API test gated behind `RUN_REAL_VOYAGE_TEST=1` (skipped by default)
**Acceptance criteria:**
- [x] `Embedder.embed(chunks) -> list[Vector]` handles text-only and text+image chunks — verified against both synthetic chunks and real `table_heavy.pdf` output; a chunk's text and image become segments of one combined multimodal input, never separate inputs
- [x] Returns 1024-dim vectors from `voyage-multimodal-3.5` — verified via mock and via one real API call against all 43 of `table_heavy.pdf`'s real chunks (see CHANGELOG for the live result)
- [x] Batches API calls — **corrected: the "128 inputs" figure above was an unverified guess, not a checked fact.** Verified via the installed SDK's source: `multimodal_embed()` has no client-side batch cap; the real server-side limits are 1,000 inputs **and** 320,000 total tokens per call (`.agent/api-docs/voyage.md`). `embedder.py` batches against both, using a 300,000-token safety budget. **Updated 2026-07-23 (Codex review):** the text-token portion of the batching estimate now uses Voyage's own real local tokenizer (`voyageai.Client.tokenizer(MODEL)`, confirmed available fully offline for `voyage-multimodal-3.5` after a one-time HF download) instead of the char/4 proxy — only the image-token portion (Voyage's documented pixel/560 formula, not an estimate) carries any residual uncertainty now, which is why the safety margin could be tightened from 280,000 to 300,000 without reintroducing the proxy's ~2.3x worst-case error risk that FEAT-005 measured.
- [x] Retries on 429 with exponential backoff (max 3 retries) — **the Voyage SDK already implements this natively** (`tenacity`-based, exponential + jitter) via `voyageai.Client(max_retries=3)`; `embedder.py` uses the SDK's own mechanism rather than hand-rolling one. Verified empirically: `max_retries=3` yields exactly 3 total attempts (not 4), confirmed by patching `voyageai.MultimodalEmbedding.create` and counting calls.
- [x] Raises `EmbedError` on non-transient failures — verified for `AuthenticationError` and `InvalidRequestError` (fail on first attempt, no retry — neither is in the SDK's retry predicate) and for retry-exhausted `RateLimitError` (fails only after 3 real attempts)
- [x] **Added 2026-07-23 (Codex review):** Response cardinality is checked against the batch before vectors are returned — `embed()` now raises `EmbedError` (with the batch's chunk-index context) if Voyage ever returns a different number of embeddings than inputs sent, rather than silently returning a partial/misaligned list. Concretely demonstrated to have been a real gap before the fix: a fake client returning 4 embeddings for 5 submitted chunks previously passed silently; `test_cardinality_mismatch_raises_embed_error_with_batch_context` and `test_cardinality_mismatch_returns_no_partial_vectors` now cover it directly, plus `test_cardinality_mismatch_across_multiple_batches_still_raises` for the multi-batch case. `test_each_chunk_maps_to_its_own_distinct_vector` closes the underlying test-double gap: the original `FakeVoyageClient` returned an identical vector for every input, which could never have caught a correspondence bug even with a correct count — it now returns distinct, index-derived vectors per input.

**FEAT-004 image ownership contract, explicitly tested:** `embedder.py` reads a chunk's `PIL.Image` to pass to Voyage's SDK (which converts it to base64 WEBP internally) but never closes it — verified both by confirming the image is still usable after `embed()` returns and by patching `PIL.Image.Image.close` and asserting it's never called.

**Real batch behavior on `table_heavy.pdf`'s 43 chunks (live API call, not simulated):** all 43 chunks fit in **1 batch** (~5,182 proxy tokens total, nowhere near the 300,000-token safety budget or the 1,000-input cap) → **1 real API call**, no retries triggered, **43 vectors returned, all confirmed 1024-dimensional**, ~3.75s elapsed. This is expected for a document this size — multi-batch behavior is proven separately via synthetic tests (`test_batches_respect_max_inputs_per_batch`, `test_batching_preserves_order_and_uses_multiple_calls`), not against a real fixture large enough to actually need 2+ batches (none of the three fixtures are).

**Run:**
- `cd apps/api && uv run pytest tests/test_embedder.py -v` (real-API test skipped unless `RUN_REAL_VOYAGE_TEST=1` is set)

**Changelog:** See CHANGELOG.md 2026-07-23 "feature: Voyage embedder wrapper (FEAT-006)"

---

### [FEAT-007] `/ingest` endpoint
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Depends on:** FEAT-003, FEAT-004, FEAT-005, FEAT-006
**Files:**
- `apps/api/routes/ingest.py` — `post_ingest`, `run_ingest_pipeline`, `get_pipeline_runner` (FastAPI `Depends` hook so tests inject fake pipeline stages via real constructor injection, not monkeypatching), `_upload_figures`, `_fail_document` (best-effort failure/cleanup handling), `validate_storage_path`/`StoragePathError` (shared authorization boundary — see the 2026-07-23 security fix below)
- `apps/api/db/queries.py` — document status-transition helpers (`create_document`, `mark_parsing`, `mark_parsed`, `mark_embedded`, `mark_ready`, `mark_failed`) and chunk persistence (`build_chunk_rows`, `insert_chunks`, `delete_chunks_for_document`)
- `apps/api/models/ingest.py` — `IngestRequest`, `IngestResponse`
- `apps/api/main.py` — wired `ingest.router` in (necessary plumbing, not in the original Files: list, same class of implicit-but-required change as FEAT-003's middleware wiring)
- `apps/api/tests/_local_supabase.py` (new) — shared local-Supabase-stack test helpers (real admin client, real user create/login, real REST/storage calls) used by both test files below
- `apps/api/tests/conftest.py` (new) — overrides `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` to the local stack's fixed CLI demo values before `main.app` is ever imported (`.env` points at the live project; STANDARDS.md requires integration tests to run against `supabase start` instead), plus `require_local_supabase`/`admin` fixtures
**Tests:**
- `apps/api/tests/test_ingest.py` — 25 tests, real local Supabase Auth+DB+Storage throughout (real users, real ES256 login, real RLS), Docling/Voyage faked (FakeParser/FakeChunker/FakeEmbedder) for speed — covers all 5 acceptance criteria below plus an extra dedicated no-partial-data test, an extra mime_type-422 test, three Codex-review hardening tests, thirteen path-traversal security-fix regression tests, and two log-leak regression tests (see below)
- `apps/api/tests/e2e/test_ingest_e2e.py` — 1 test, fully real pipeline (real Docling parse, real Voyage embed call, real chunk insert, real figure upload, raw pgvector similarity query), gated behind `RUN_INGEST_E2E_TEST=1` (skipped by default — slow, spends Voyage quota, same pattern as `test_embedder.py`'s real-API test)
**Acceptance criteria:**
- [x] POST /ingest with valid body returns 202 + document_id — `test_post_ingest_with_valid_body_returns_202_document_id`; verified the response's `status` field is the literal DB value at insert time (`"uploaded"`) — API_CONTRACT.md's example previously showed `"parsing"`, corrected (see CHANGELOG)
- [x] storage_path prefix mismatch with JWT user_id returns 403 — `test_storage_path_prefix_mismatch_with_jwt_user_id_returns_403`; also confirmed no `documents` row is created (check happens before anything else)
- [x] Background task: download → parse → chunk → embed → insert → update status — `test_background_task_download_parse_chunk_embed_insert_update_sta`; verified status reaches `ready`, `page_count`/`parsed_at`/`embedded_at` populated, a text chunk and a figure chunk both land correctly (figure actually present in Storage, not just a recorded path), and the figure's `PIL.Image` is closed after use (FEAT-004's ownership contract)
- [x] Failure at any stage sets documents.status = 'failed' with error message — `test_failure_at_any_stage_sets_documents_status_failed_with_error`, mocking the embedder to fail; **plus this task's explicit depth requirement**, proven concretely rather than assumed: `test_no_partial_chunk_rows_after_embedder_fails_partway` gives the fake embedder 5 chunks and has it fail after "computing" 3, then confirms zero rows in `chunks` for that document — not just that `status='failed'`. Architecturally guaranteed, not just tested-around: all chunk rows for a document are written in exactly one bulk INSERT (a single atomic Postgres statement), and every stage that can fail happens strictly before that call
- [x] Multi-tenant isolation: user A cannot see user B's document via any query path — `test_multi_tenant_isolation_user_a_cannot_see_user_b_s_document_v`, two real users via real local Auth, checked via three real RLS-scoped REST query paths (list, direct ID lookup, chunk search) since `GET /documents` doesn't exist yet (FEAT-008's job) — this test hits `/rest/v1/documents` and `/rest/v1/chunks` directly with each user's own access token. Includes a **positive control**: confirmed user A can see their own document/chunks via all three paths first, so an empty result for user B actually proves isolation rather than "RLS blocks everyone" going unnoticed (see .agent/MEMORY.md's circular-verification anti-pattern from FEAT-003 — same discipline applied here)

**Real end-to-end numbers (`table_heavy.pdf`, live Docling + live Voyage + local Postgres, not simulated):** POST → pipeline-complete in **72.70s**, `status: ready`, `page_count: 11`, **43 chunks inserted** (matches FEAT-005/006's known count for this fixture), 0 figure chunks (this fixture has none). Raw pgvector query against the actually-inserted rows: a chunk's own embedding as the probe vector returns itself as nearest neighbor at cosine distance **0.000000**, next-nearest real chunks at **0.49–0.51** — confirms the stored vectors are real, round-trip correctly through the REST insert, and are meaningfully differentiated (not degenerate).

**Codex review follow-up, 2026-07-23 — defense-in-depth hardening:** review found `run_ingest_pipeline()`'s correctness depended on invariants (`storage_path` belongs to `user_id`; dependencies construct successfully) that were only ever true because its one caller (`post_ingest`) happened to check first — the function itself trusted both unconditionally. Fixed: the storage_path/user_id check now runs again inside `run_ingest_pipeline()` itself (`IngestInvariantError` if violated), and `client`/`Parser`/`Chunker`/`Embedder` construction moved inside the `try` block so a construction failure gets caught and marks the document `failed` instead of leaving it stuck at `uploaded` with zero signal. The except block's own cleanup calls (`delete_chunks_for_document`, `mark_failed`) are now each wrapped independently in a best-effort try, logging at ERROR with full context if cleanup itself fails, rather than letting a second failure go unnoticed. Three new tests prove each of these directly: `test_run_ingest_pipeline_refuses_mismatched_user_id_and_storage_path` (calls the function directly, bypassing the route), `test_dependency_construction_failure_marks_document_failed` (patches `Parser` to fail construction), `test_cleanup_failure_is_logged_and_does_not_crash_pipeline` (an always-failing fake client, asserts the pipeline call itself doesn't raise and both cleanup failures are logged). General lesson logged in `.agent/MEMORY.md §Anti-patterns`: "no current caller violates an invariant" is not the same claim as "the invariant is enforced." Background-task durability (timeouts, dead-letter, crash recovery) was confirmed as a real, separate gap but deliberately deferred to Phase 5 — logged as an explicit out-of-scope entry in `.agent/SCOPE.md` rather than patched incrementally here.

**SECURITY FIX, 2026-07-23 — path traversal in storage_path (High severity, confirmed exploitable):** a proactive self-audit found `storage_path.startswith(f"uploads/{user_id}/")` — the ownership check both above call sites relied on — could be defeated by `"uploads/{attacker}/../{victim}/file.pdf"`: the string check passes (it genuinely starts with the attacker's own prefix), and Supabase Storage's own server resolves the `".."` when fetching the object, so the privileged service-role download call returned the *victim's real file content* — confirmed via both the storage3 SDK and raw HTTP against the real local stack. A first fix attempt (reject raw `".."` segments, require `posixpath.normpath` equality) was **also** proven exploitable live — a percent-encoded variant (`%2e%2e%2f`) bypassed both checks and still resolved to the victim's file. The fix that held up under live re-testing: `validate_storage_path()` (new, shared by both `post_ingest()` and `run_ingest_pipeline()`) checks the trusted, server-built `"uploads/{user_id}/"` prefix, then requires everything after it to match a character whitelist (`^[A-Za-z0-9._-]+$`) with no path-separator character — literal or encoded — possible at all, closing the whole bypass class rather than pattern-matching known-dangerous spellings. Proven at four levels: a unit-level matrix, route-level 403 tests, pipeline-level failed-status tests, and a live re-run of the exact recon proof-of-concept in reverse (real victim upload, real attacker request, confirmed blocked before any Storage call). Full write-up: CHANGELOG.md 2026-07-23 "SECURITY — path traversal..." entry and `.agent/MEMORY.md §Anti-patterns`.

**Security review follow-up, 2026-07-23 — raw storage_path no longer reaches server logs:** review found both `post_ingest()` and `run_ingest_pipeline()` logged the full attacker-supplied `storage_path` on a rejected traversal attempt (the 403 response itself stayed generic, but the raw string — another user's UUID, encoded probes, arbitrary text — landed in server logs, a real concern if those are hosted externally). `StoragePathError` now carries a coarse `reason` category separate from its detailed message; both log sites log only `user_id`/`document_id` + `reason`, never the exception or its message. `documents.error` (DB, RLS-scoped, not a log) is unchanged. Verified the same way the review itself checked: two new tests capture real log output via `caplog` after a triggered traversal attempt and assert the raw path never appears, plus a manual `-s --log-cli-level=WARNING` run read directly by eye.

**Known, accepted gaps (flagged, not fixed here — out of this task's scope):**
- Figure objects uploaded to Storage before a later stage fails are not cleaned up — SCOPE.md's no-partial-data guarantee is scoped to the `chunks` table specifically, not Storage objects
- Pydantic-level 422s (malformed JSON body) don't yet match API_CONTRACT.md's standard error envelope — only this endpoint's own application-level 403/422 checks do; a global exception handler would need to touch `main.py` more broadly, cross-cutting every route, not just this one
- Background-task durability (timeouts, dead-letter, crash recovery) — deferred to Phase 5, see `.agent/SCOPE.md`

**Run:**
- `supabase start` (local stack), then `cd apps/api && uv run pytest tests/test_ingest.py -v`
- Full real pipeline: `RUN_INGEST_E2E_TEST=1 uv run pytest tests/e2e/test_ingest_e2e.py -v -s`
- Manual: upload a PDF to Supabase Storage under `uploads/{user_id}/`, then `curl -X POST -H "Authorization: Bearer <jwt>" -d '{"storage_path":"...","filename":"...","mime_type":"application/pdf","size_bytes":123}' http://localhost:8000/ingest`

**Changelog:** See CHANGELOG.md 2026-07-23 "feature: `/ingest` endpoint, full pipeline wiring (FEAT-007)"

---

### [FEAT-008] `/documents` list + detail + delete
**Phase:** 1
**Status:** complete
**Owner:** claude-code
**Depends on:** FEAT-007
**Files:**
- `apps/api/routes/documents.py` — `list_documents`, `get_document`, `delete_document`
- `apps/api/db/queries.py` — added `get_document`, `list_documents`, `list_figure_paths_for_document`, `delete_document`, `remove_document_from_conversations`
- `apps/api/models/documents.py` — `DocumentResponse`, `DocumentListResponse`
- `apps/api/main.py` — wired `documents.router` in
- `apps/api/tests/conftest.py` — the `FakeParser`/`FakeChunker`/`FakeEmbedder`/`user_a`/`user_b`/`app_client` fixtures and helpers previously local to `test_ingest.py` moved here so both test files share one implementation (no duplication, no cross-file import); added `ingest_real_document()`, the shared "run a real document through the real /ingest endpoint" helper this feature's tests and future ones can reuse
**Tests:**
- `apps/api/tests/test_documents.py` — 8 tests, every one ingests its document through the real `/ingest` endpoint first (never a hand-inserted row, except the one `status='parsing'` test — see below), real local Supabase Auth+DB+Storage throughout — includes two storage-failure-handling regression tests added in the self-verification follow-up (see below)
**Acceptance criteria:**
- [x] GET /documents returns paginated list scoped to JWT user — `test_get_documents_returns_paginated_list_scoped_to_jwt_user`; also verifies the response shape matches API_CONTRACT.md exactly, the `status` filter, and that invalid `status`/`cursor` query values get a clean 422 rather than surfacing a raw Postgres enum-cast error
- [x] GET /documents/{id} returns 404 for another user's doc — `test_get_documents_id_returns_404_for_another_user_s_doc`; asserts the response for "belongs to another user" and "genuinely doesn't exist" are byte-identical (same status code, same body) — no 403-vs-404 distinction to leak existence, same discipline as FEAT-007's `storage_path` hardening. `get_document()`'s query scopes `user_id` in the query itself (`.eq("id", ...).eq("user_id", ...)`), not checked after fetching by id alone, so there's structurally nothing to leak
- [x] DELETE /documents/{id} cascades to chunks, conversations reference, storage files — `test_delete_documents_id_cascades_to_chunks_conversations_referen`; ingests a document with a real figure chunk, inserts a synthetic `conversations` row referencing it (Phase 2 doesn't exist yet, so this is the only way to exercise that path), deletes, and confirms all four: `documents` row gone, `chunks` rows gone (DB-level FK cascade), the `conversations.document_ids` array no longer contains it (application-level cleanup — arrays aren't FK-cascadable), **and** both Storage objects (the uploaded PDF and the figure PNG) are confirmed actually gone by attempting to download them directly from the bucket afterward and asserting that raises — not inferred from "the delete call didn't error" (task's explicit item 5 requirement)
- [x] DELETE while status='parsing' returns 409 — `test_delete_while_status_parsing_returns_409`. The one test in this file that inserts a document row directly rather than through `/ingest`: a document genuinely stuck mid-parse isn't reproducible through the real endpoint under `TestClient`, since the background task always runs to completion synchronously before the HTTP call returns — this is a deliberate, documented exception to the file's own rule, not an oversight

**Multi-tenant isolation across all three endpoints (task item 4), same live-verification discipline as FEAT-007:** `test_multi_tenant_isolation_across_list_get_delete` — two real users, one real ingested document. Positive control first (user A can list/get their own document), then user B: absent from the list, 404 on direct GET, 404 on DELETE — **and** confirms user B's rejected DELETE did not actually remove user A's document (checked directly against the DB afterward, not assumed from the 404 response alone).

**Design notes:**
- Both `post_ingest`'s (FEAT-007) and every `/documents` endpoint's ownership checks use the service-role client with an explicit `.eq("user_id", user_id)` filter baked into the query itself, not RLS via a user-scoped client — consistent with FEAT-007's established pattern, and functionally equivalent to RLS for the non-leak guarantee since a query scoped this way returns zero rows for both "doesn't exist" and "wrong owner" cases identically.
- Pagination cursor is `base64(created_at)` alone, not a full `(created_at, id)` keyset tiebreak — documented, accepted simplification: two documents sharing a byte-identical microsecond-precision timestamp isn't realistic given documents are created via separate sequential API calls, and a full compound-OR keyset filter would add real complexity for a case that doesn't occur in practice at this project's scale.
- Storage cleanup happens *before* the DB delete in `delete_document`, deliberately the opposite order from `run_ingest_pipeline`'s figure uploads: a genuine Storage failure here propagates as an error with the document row still intact, rather than risking a split-brain state where the DB row is already gone but Storage objects are orphaned with no remaining record of which document they belonged to.
- `API_CONTRACT.md` was missing a `CONFLICT`/409 error code entry despite `DELETE /documents/{id}`'s own spec already documenting a 409 response — added.

**Self-verification follow-up, 2026-07-23 — Storage-failure error handling:** a live self-verification pass (re-running the feature's own claims rather than re-asserting them) found `delete_document`'s two Storage `.remove()` calls had no error handling — a simulated partial failure (figures removal throwing, uploads succeeding) confirmed the underlying retry-safety was already correct (document/chunks survive, a retry succeeds cleanly, removing an already-gone object doesn't itself error) but the first failure surfaced as a bare unhandled 500 with no envelope and no log line. Fixed: both `.remove()` calls now wrapped individually, returning the standard error envelope with a new `STORAGE_ERROR` (500) code whose message tells the caller the document wasn't modified and retrying is safe; the ERROR log records only `document_id`/`user_id`/which bucket failed, never the exception's own message (same coarse-reason discipline as FEAT-007's `StoragePathError` fix, since a Storage error's message isn't guaranteed path-free). Two new tests turn the live check into a permanent regression test: one reproducing the exact failure and asserting the envelope/log/survival, one continuing into a retry and asserting clean `204` completion. `API_CONTRACT.md` gained the `STORAGE_ERROR` entry. Full write-up: CHANGELOG.md 2026-07-23 "delete_document Storage failures..." entry.

**Run:**
- `curl -H "Authorization: Bearer <jwt>" http://localhost:8000/documents`
- Tests: `cd apps/api && uv run pytest tests/test_documents.py -v`

**Changelog:** See CHANGELOG.md 2026-07-23 "feature: `/documents` list + detail + delete (FEAT-008)"

---

## Phase 2 — Retrieval + Generation

### [FEAT-009] Retriever service (hybrid + RRF)
**Phase:** 2
**Status:** complete
**Owner:** claude-code
**Depends on:** FEAT-001, FEAT-005, FEAT-006, FEAT-007
**Files:**
- `apps/api/services/retriever.py` — `Retriever`, `RetrievedChunk`, `RRF_K` (=60), `DEFAULT_K` (=8), `_reciprocal_rank_fusion`. **2026-07-27 rerank follow-up:** `Reranker`, `RERANK_MODEL` (="rerank-2.5"), `RERANK_POOL_SIZE` (=20); `Retriever.retrieve()` gained an opt-in `rerank: bool = False` parameter.
- `apps/api/services/embedder.py` — new `Embedder.embed_query(text) -> Vector` method (query-side embedding, `input_type="query"`; distinct from `embed()`'s ingestion-side `input_type="document"` — Voyage's embeddings are asymmetric, see `.agent/api-docs/voyage.md`). Not in the original Files: list, but necessary — there was no existing way to embed a bare query string with the correct input_type.
- `apps/api/migrations/20260724_001_hybrid_search_functions.sql` (new, not in the original Files: list either, same "necessary plumbing" class as FEAT-002/007/008's router wiring) — `match_chunks_by_vector` and `match_chunks_by_fts` Postgres functions. Neither pgvector cosine ranking (`embedding <=> query_embedding`) nor FTS ranking (`ts_rank`) is expressible through PostgREST's REST query builder (`.select()`/`.order()` only support plain column comparisons, not arbitrary SQL expressions in `ORDER BY`) — the standard, documented way to do this through Supabase's REST layer is a Postgres function called via `client.rpc(...)`, not raw SQL from app code. `EXECUTE` revoked from `PUBLIC` (Postgres's default), granted only to `service_role`.
- `.agent/api-docs/voyage.md` — new "Reranking" section (2026-07-27), verified live + against installed SDK source, same discipline as the rest of the file.
**Tests:**
- `apps/api/tests/test_retriever.py` — 10 tests originally (8 fast/structural + 1 gated real quality test). **2026-07-27 rerank follow-up added 13 more:** 9 fast/structural (Reranker unit tests with a fake Voyage client — success/empty/error/malformed-response/lazy-construction; Retriever wiring tests — opt-in cost guard, result-ordering-on-success, fallback-on-failure, pool-size-not-just-k) plus 1 more real quality/latency test gated behind `RUN_RETRIEVAL_QUALITY_TEST=1` (23 tests total; 21 fast, 2 gated real).
**Acceptance criteria:**
- [x] `Retriever.retrieve(question, document_ids, user_id, k) -> list[RetrievedChunk]` — `test_retriever_retrieve_question_document_ids_user_id_k_list_chun`. Returns `RetrievedChunk`, not `chunker.Chunk` — deliberately a different type (ingestion-time chunk vs. a ranked search result with an id, fused score, and document name), documented in the dataclass's own docstring
- [x] Runs vector search (cosine) and BM25 (Postgres FTS) in parallel — `test_runs_vector_search_cosine_and_bm25_postgres_fts_in_parallel`, proven via real wall-clock timing (two 0.4s-sleeping fake search methods complete in ~0.4s total, not ~0.8s), not just asserting both got called. Uses a `ThreadPoolExecutor` — the one place in this codebase that does real thread-level concurrency, since these are two genuinely independent read-only queries (see `.agent/reviews/2026-07-23-efficiency.md`'s note that most of this codebase's request handling is actually fully synchronous despite `async def`)
- [x] Merges via Reciprocal Rank Fusion (k=60 default) — `RRF_K = 60`, verified distinct from `DEFAULT_K = 8` (the retrieval count) by name, by test, and by a doc-comment on `RRF_K` calling out the exact confusion risk this task flagged. `test_merges_via_reciprocal_rank_fusion_k_60_default` hand-computes expected fused scores for a case where vector search's own #1 pick and FTS's own #1 pick both individually lose to a chunk that ranked #2 (not #1) in *both* — the textbook demonstration of why fusion beats either single ranking
- [x] Returns top-k with metadata: chunk_id, content, page, document_name, element_type — `test_returns_top_k_with_metadata_chunk_id_content_page_document_n`, asserts every field round-trips exactly and that top-k genuinely limits (a second, non-matching chunk is correctly excluded)
- [x] user_id is included in every SQL WHERE clause explicitly — both `match_chunks_by_vector`/`match_chunks_by_fts` take `match_user_id` as an explicit function parameter and filter on it directly (see the migration), not relying on RLS (these functions are only ever called with the service-role client, which bypasses RLS entirely — the explicit filter is the actual tenant boundary, matching FEAT-007/008's established pattern). `test_user_id_is_included_in_every_sql_where_clause_explicitly` spies on the real `.rpc()` calls and asserts `match_user_id` is present in both
- [x] **(2026-07-27 follow-up)** Optional post-RRF reranking via Voyage `rerank-2.5` — `retrieve(..., rerank=True)`; RRF's top `RERANK_POOL_SIZE=20` candidates sent to Voyage, real top-k returned in Voyage's relevance order. `test_rerank_true_uses_the_rerankers_result_ordering_when_it_succeeds`, `test_rerank_true_sends_the_rerank_pool_not_just_the_final_k`
- [x] **(2026-07-27 follow-up)** Reranking is opt-in, never triggered unless explicitly requested — `test_rerank_is_opt_in_reranker_never_called_when_rerank_not_requested` asserts zero reranker calls when `rerank` is left at its `False` default
- [x] **(2026-07-27 follow-up)** Any rerank failure (network, auth, quota, malformed response) falls back to RRF's own ranking, never crashes, never returns nothing — `test_rerank_true_falls_back_to_rrf_ranking_when_reranker_fails`, plus `Reranker`-level unit tests for a real `VoyageError` and a malformed response shape

**Multi-tenant isolation, same live-verification discipline as FEAT-007/008:** `test_multi_tenant_isolation_excludes_other_users_chunks` — two real users, **identical content and identical (maximally-matching) embedding** in both users' chunks (the strongest possible test: if scoping didn't work, user B's chunk would tie or beat user A's, not just "also show up"). Confirms user B's chunk never appears in user A's results, plus a positive control (user B can retrieve their own identical-looking chunk when correctly scoped to their own document/user_id) proving the absence is real scoping, not a broken query returning nothing for anyone. A second test (`test_document_ids_scoping_excludes_the_same_users_other_documents`) confirms `document_ids` scoping specifically — a second document owned by the *same* user is excluded when not named in `document_ids`.

**Retrieval quality — the high-depth part, real results, not just pass/fail:** real `table_heavy.pdf` ingested through the real `Parser`/`Chunker`/`Embedder` (real Docling parse, real Voyage embeddings), 4 questions built from content read directly out of that real ingestion's actual chunk text (not assumed from memory of an earlier session), each with a manually-verified "this chunk should be in the top-k" expectation:

| Question | Expected content | Result |
|---|---|---|
| What is Angola's Human Development Index value in 2010? | Table 19 (HDI), Angola row | **rank 1** of 5 |
| Does Respondent C have a driving licence? | Table 14, "Do you have a driving licence?" row | **rank 1** of 5 |
| What was the balance in the 2011 accounts? | Table 18 (accounts, 2011), "Balance" row | **rank 2** of 5 (Table 17, an earlier draft of the same accounts table sharing the same £-figures, ranked 1st — both are legitimately about the same balance) |
| What courses does Institution X offer in Mathematics? | Table 15 (courses, Institution X), Mathematics row | **rank 2** of 5 (Table 16, "Masters courses offered by Institution X", ranked 1st — same institution, closely related table) |

All 4/4 found their expected chunk in the top-5; 2/4 at rank 1, 2/4 at rank 2 (both times narrowly edged out by a genuinely closely-related table from the same document, not an irrelevant result). Full ranked output for all 4 questions is in the test's own `print()` output (`pytest tests/test_retriever.py::test_retrieval_quality_against_real_table_heavy_pdf -s`) and in CHANGELOG.md's entry for this feature.

**A real infrastructure constraint hit and worked around honestly, not silently:** the quality test originally made 5 real Voyage calls in quick succession (1 document-embed for ingestion + 4 query-embeds) and hit a genuine `RateLimitError` — this Voyage account has no payment method on file, capping it at 3 RPM. Fixed by pacing real calls 25s apart in the test itself, with a comment explaining why — not a workaround for a bug in `Retriever` or `Embedder`, a real account-level constraint external to this code.

**Run:**
- `pytest apps/api/tests/test_retriever.py -v` (fast, 8 structural tests)
- `RUN_RETRIEVAL_QUALITY_TEST=1 pytest apps/api/tests/test_retriever.py::test_retrieval_quality_against_real_table_heavy_pdf -v -s` (slow, real Voyage calls, ~3.5 min due to rate-limit pacing)

**2026-07-27 follow-up — optional Voyage reranking (`rerank-2.5`):**

**Design decision — opt-in, not default-on:** `.agent/MEMORY.md`'s original "Rerank in Phase 2 or Phase 4" leaning said to measure quality without reranking first and only add it if RRF alone proved insufficient. FEAT-009's own real quality fixture found RRF alone already lands the expected chunk in the top-5 4/4 times (2/4 at rank 1, 2/4 at rank 2, both times narrowly beaten by a genuinely closely-related table). Given that, reranking shipped as a caller-toggled `rerank: bool = False` parameter on `retrieve()`, not always-on — the cost (an extra real Voyage API call, ~380ms measured below) needs to earn its place against a baseline that already isn't broken, not be paid on every query by default. `test_rerank_is_opt_in_reranker_never_called_when_rerank_not_requested` locks this in as a permanent regression test, not just a one-time design note.

**Real evidence from re-running the exact same 4 quality questions (`test_reranking_effect_on_real_table_heavy_pdf_quality_questions`, gated behind `RUN_RETRIEVAL_QUALITY_TEST=1`):**

| Question | Baseline (RRF) rank | Reranked rank | Result |
|---|---|---|---|
| Angola HDI 2010 | 1 | 1 | SAME |
| Respondent C driving licence | 1 | 1 | SAME |
| 2011 accounts balance | 1 | 1 | SAME |
| Institution X Mathematics courses | 1 | 1 | SAME |

**Honest caveat, not swept under the rug:** this run's RRF baseline (4/4 at rank 1) is actually *better* than FEAT-009's originally-recorded baseline (2/4 at rank 1, 2/4 at rank 2) for the same 4 questions against the same fixture. Most likely cause: the two previously-rank-2 cases were narrowly beaten by a closely-related duplicate/near-duplicate table, and Postgres's tie-breaking among near-equal RRF scores isn't guaranteed stable run-to-run (this test ingested the fixture fresh into a new `document_id`, a new set of chunk rows). This wasn't chased down further (would require diffing raw fused scores across two runs, out of this follow-up's scope) but is flagged rather than hidden: it means this particular run's baseline was already at a ceiling (rank 1, nothing to improve), so reranking's real upside case (promoting a rank-2+ result to rank 1) wasn't actually exercised here. What the run does honestly confirm: reranking did not make anything *worse* — a real, meaningful result in its own right, since a reranker that reshuffles a good baseline for the worse would be a net loss.

**Real added latency (task item 5, feeds `.agent/reviews/2026-07-24-full-flow.md`'s latency budget):** mean **380.8ms** (min 354.8ms, max 408.4ms, n=4 real Voyage `rerank-2.5` calls, k=5, pool size ≤20) on top of the existing 401.9ms retrieval figure — i.e. retrieval+rerank together run ~782.7ms when a caller opts in, vs 401.9ms when they don't. Pushes the worst-case `/query` total from ~8.3s to ~8.68s and best-case from ~4.0s to ~4.4s **only for callers that pass `rerank=True`** — nothing currently does (no route wires this in yet; it's available, not yet adopted).

**Fail-safe verified two ways:** (1) `Reranker.rerank()` unit-tested directly against a fake Voyage client raising a real `RateLimitError` and against a malformed response missing `.results` — both return `None`, logged as a WARNING, never raise. (2) `Retriever.retrieve(rerank=True)` with a reranker forced to return `None` produces byte-identical output to the same call with `rerank=False` — the fallback isn't just "doesn't crash," it's "produces exactly RRF's own answer," confirmed by `test_rerank_true_falls_back_to_rrf_ranking_when_reranker_fails`.

**Cost guard confirmed:** `test_rerank_is_opt_in_reranker_never_called_when_rerank_not_requested` asserts zero `Reranker.rerank()` calls when `rerank` is left at its default — reranking cannot fire on a plain `retrieve()` call, matching the stated opt-in design exactly (no drift between stated and actual behavior).

**API verified live before coding, not assumed:** model name (`rerank-2.5`, no SDK default — `model` is a required parameter), limits (1,000 docs/call, 32K query+doc tokens, 600K total tokens/call), and response shape (`.results[].index/.document/.relevance_score`, already sorted descending by relevance) all confirmed via a real live call plus the installed SDK source, not the docs page alone — `.agent/api-docs/voyage.md`'s new "Reranking" section. Same `VOYAGE_API_KEY`/`voyageai.Client` already used for embeddings — no new credential, so no `.env.example` change needed.

**A real, second instance of FEAT-017's eager-credential-crash bug pattern found (not fixed here, out of scope):** confirmed live that bare `voyageai.Client()` raises `AuthenticationError` immediately if `VOYAGE_API_KEY` is absent — the same shape of bug the FEAT-017 OCR audit found and fixed for `GeminiOcrClient`/`OcrSpaceClient`. The new `Reranker` class was built lazy from the start to avoid introducing a third instance of it (`test_reranker_construction_never_touches_network_or_requires_api_key` locks this in). However, `Embedder.__init__` (pre-existing, FEAT-006) and by extension a bare `Retriever()` (which builds a default `Embedder()`) still construct their `voyageai.Client` eagerly — meaning `Retriever()`/`Embedder()` today would crash in an environment missing `VOYAGE_API_KEY`, exactly like the pre-audit `GeminiOcrClient` did. Not fixed as part of this follow-up (out of stated scope — this task was reranking, not an `Embedder` audit); flagged here so it isn't lost. Candidate for its own small follow-up.

**Changelog:** See CHANGELOG.md 2026-07-24 "feature: Retriever service, hybrid search + RRF (FEAT-009)" and 2026-07-27 "feature: optional Voyage reranking, opt-in (FEAT-009 follow-up)"

---

### [FEAT-010] Gemini generator wrapper
**Phase:** 2
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/services/generator.py`
- `.agent/api-docs/gemini.md` (via `/api-check gemini`)
**Tests:**
- `apps/api/tests/test_generator.py`
**Acceptance criteria:**
- [x] `Generator.generate(question, chunks) -> GenerateResult` returns answer text + parsed citation markers
- [x] System prompt instructs Gemini 3.6 Flash to cite chunk IDs inline as `[N]`
- [x] Multimodal — figure chunks pass their image content to Gemini
- [x] Returns metadata: model, input_tokens, output_tokens, latency_ms
- [x] `[N]` markers are positions in the `chunks` list passed to `generate()` (1-indexed), NOT chunk_id — FEAT-012 must map `chunks[N-1].chunk_id` itself. `GeneratorChunk` is Generator's own input type (not FEAT-009's `RetrievedChunk`, which carries no image data); FEAT-012 must fetch each figure chunk's image from Storage via `figure_path` and adapt `RetrievedChunk` rows into `GeneratorChunk` before calling `generate()`.
- [x] Hallucinated citation markers (out of `1..len(chunks)` range) never crash `generate()` — dropped from `cited_indices`, surfaced in `GenerateResult.hallucinated_markers` and logged.

**Run:**
- Before implementation: `/api-check gemini`

---

### [FEAT-011] Citation verifier
**Phase:** 2
**Status:** complete
**Owner:** claude-code
**Files:**
- `apps/api/services/verifier.py`
**Tests:**
- `apps/api/tests/test_verifier.py` — supported / partial / unsupported fixtures
**Acceptance criteria:**
- [x] `Verifier.verify(claim, chunk) -> Verdict{verdict, quote}` uses Gemini 3.5 Flash-Lite
- [x] Verdict enum: supported | partial | unsupported
- [x] Returns the supporting quote from the source (or null if unsupported) — enforced defensively (quote forced `None` for `unsupported` even if the model deviates and emits one), not just prompted
- [x] Batches verifications per generate call — `Verifier.verify_batch(pairs)` runs one Gemini call per `(claim_text, chunk)` pair concurrently (`ThreadPoolExecutor`, same shape as FEAT-009's retriever), preserving input order regardless of completion order
- [x] `verify()` takes an already-resolved `(claim_text, chunk: GeneratorChunk)` pair — it does not do `[N]`-to-chunk resolution itself; that mapping (`chunks[N-1].chunk_id`) is FEAT-012's job, same principle as FEAT-010's citation markers. Reuses `GeneratorChunk` rather than introducing a third near-identical chunk type, since by verification time FEAT-012 already has `GeneratorChunk`s (images already fetched) built for the `generate()` call.
- [x] Structured output (`response_schema`/`response_mime_type="application/json"`, a pydantic model) is used instead of free-text regex parsing, closing the entire failure class FEAT-010's `[N]`-marker parsing needed three rounds of live fixes to close — verified against installed SDK source (`.agent/api-docs/gemini.md`).
- [x] **Fails safe, proven not assumed:** any failure to verify a claim — a real Gemini API error (proven live against an actual invalid-API-key auth failure, not just a mock), a genuine transport-layer timeout/connection failure (`httpx.HTTPError`, a real gap this session's self-audit found: the SDK's own HTTP layer never wraps these into `APIError`, so a bare `except APIError` let a mocked `httpx.ReadTimeout` crash `verify()` uncaught before this was fixed), or a response that doesn't conform to the schema (the SDK silently sets `response.parsed = None` in this case rather than raising, confirmed via SDK source read) — is converted into a forced `Verdict(verdict=UNSUPPORTED, quote=None, error=<reason>)`, never an exception a caller could mishandle and never a silent pass-through as verified. A caller that only reads `.verdict` and never checks `.error` still gets the safe outcome by construction.
- [x] **The returned quote is verified against the real chunk content, not trusted as-is** — a 2026-07-24 self-audit found this check was entirely missing at ship time: a mocked `verdict=SUPPORTED` response with a plausible-sounding but fabricated quote (never appearing in the source) passed straight through as "verified," with nothing catching it. `_quote_is_grounded()` normalizes whitespace (tolerating real markdown-table padding differences, not requiring byte-for-byte match) and confirms the quote is actually a substring of `chunk.content`; if not, `verify()` fails safe to `UNSUPPORTED` exactly like a broken API call — the verdict is only as trustworthy as the quote it's based on.
- [x] `response_schema`'s enum enforcement is confirmed genuinely client-side (pydantic validating the raw JSON text locally, proven by constructing `_VerdictResponse.model_validate_json()` directly with an out-of-enum string and observing the real `ValidationError`), not merely "the API happens to behave" — a garbage verdict string from any future API version collapses into the same, already-handled `response.parsed is None` path, not a new unhandled shape.

**Run:**
- `pytest apps/api/tests/test_verifier.py -v`
- Real API tests: `RUN_REAL_VERIFIER_TEST=1 pytest apps/api/tests/test_verifier.py -v -s` (single-claim smoke test, real-invalid-key fail-safe proof, adversarial `table_heavy.pdf` fixture, real 5-call concurrent batch latency)
- Compound end-to-end: `RUN_VERIFICATION_QUALITY_TEST=1 pytest apps/api/tests/test_verifier.py -k verifies_a_real_generated -v -s` (real retrieval + generation + verification, confirms a FEAT-010 positionally-correct citation is also factually verified)

---

### [FEAT-012] `/query` endpoint
**Phase:** 2
**Status:** complete
**Owner:** claude-code
**Depends on:** FEAT-009, FEAT-010, FEAT-011
**Files:**
- `apps/api/routes/query.py`
- `apps/api/services/figure_fetcher.py` (new — audit item 3, confirmed nothing existed to reuse)
- `apps/api/db/queries.py` (`documents_owned_by_user`, `get_conversation`, `create_query_turn` added)
- `apps/api/migrations/20260724_002_query_persistence_function.sql` (new — atomic conversation/message/citations RPC, same PostgREST-can't-do-cross-table-transactions reasoning as FEAT-009's hybrid search functions)
- `apps/api/services/retriever.py` (`RetrievedChunk.document_id` added — was fetched by the RPC functions but silently dropped before this feature; the response's citation shape needs it)
- `apps/api/services/generator.py` (`CITATION_BRACKET`/`CITATION_NUMBER` made public — shared, not duplicated, with `/query`'s claim-span extraction)
**Tests:**
- `apps/api/tests/test_query.py`
- `apps/api/tests/e2e/test_query_e2e.py`
**Acceptance criteria:**
- [x] POST /query returns 200 with answer + citations per API_CONTRACT.md
- [x] Unsupported citations are dropped from response, markers stripped from answer text
- [x] Creates conversation + message + citations rows atomically
- [x] Continuing an existing conversation appends messages correctly
- [x] Cross-tenant document_ids in request → 403
- [x] `Retriever.retrieve()`'s `user_id` arg is passed `request.state.user_id` (JWT-verified, FEAT-003 middleware) — never a request-body/query-param value. `QueryRequest` (`models/query.py`) has no `user_id` field at all — structurally, not just conventionally, there is no other value that could reach it. Tested adversarially on its own (spying on the real call args), independent of the full end-to-end isolation test.
- [x] Each of `Generator.generate()`'s `GenerateResult.cited_indices` (1-indexed positions into the `chunks` list, NOT chunk ids) is mapped back to a real `chunk_id` via `chunks[position - 1].chunk_id`. `services/figure_fetcher.py.fetch_generator_chunks()` adapts `RetrievedChunk` rows into `GeneratorChunk`s, fetching each figure chunk's image from Storage via `figure_path` first (confirmed by the 2026-07-24 full-flow audit that nothing did this before — built fresh, tested directly).
- [x] This route is the first production caller of `Verifier`. It owns claim-span extraction (`_extract_claim_spans()` — sentence-boundary based, reuses `Generator`'s own `CITATION_BRACKET`/`CITATION_NUMBER` regex rather than a second, independently-maintained one) and marker-stripping (`_strip_dropped_markers()` — rebuilds grouped brackets like `[1, 2]` to keep only surviving positions, not a naive per-marker string replace, since Gemini has been observed live producing that grouped shape). Per `.agent/ARCHITECTURE.md`'s verify flow: `supported` → normal; `partial` → kept with a warning indicator, never dropped; `unsupported` (including a `Verdict.error` — a failed/unverifiable Gemini call, already forced to `UNSUPPORTED` by `Verifier` itself) → dropped from the response, marker stripped, but still persisted to `citations` for audit.

**2026-07-24 full-flow audit findings, resolved:**
- Item 3 (figure-fetch) and item 4 ([N]→chunk_id resolution) were both confirmed to not exist anywhere — built fresh in `services/figure_fetcher.py` and `routes/query.py`'s `_extract_claim_spans()`/`_strip_dropped_markers()`.
- Item 5 (`API_CONTRACT.md` never documented `partial`) — fixed in a standalone doc commit before this feature was implemented; `test_partial_verdict_citations_are_kept_not_dropped` now guards the equivalent code-level mistake.
- Item 6 (latency budget, ~4–8.3s predicted) — confirmed live against the real endpoint: 3.4s–9.3s across the 4-question real quality fixture, consistent with the audit's prediction. Deliberately kept synchronous (see Decision below), not streaming.
- Item 1 (user_id trust concentration) — this route is the single place that concentration mattered; the adversarial test above targets exactly that call site.
- One NEW gap found while implementing (not in the original audit): `RetrievedChunk` never exposed `document_id`, even though `match_chunks_by_vector`/`match_chunks_by_fts` already `SELECT` it — the citation response requires it (API_CONTRACT.md), so `RetrievedChunk` gained the field (additive, no behavior change, `test_retriever.py` unaffected).

**Decision — synchronous, not streaming, and why:** `API_CONTRACT.md`'s `/query` contract already specifies a single synchronous 200 response with the full answer/citations/metadata body (locked in before this feature, not renegotiated here). Introducing SSE/streaming would be a breaking contract change with no ready consumer yet — the chat UI (FEAT-01x, Phase 3, `claude-design`-owned) hasn't been built, so the streaming *shape* is a decision better made together with whichever feature actually renders it, not guessed at here. Real measured latency (3.4s–9.3s) is slow enough to need a client-side loading state, but not so slow that a synchronous call with one is unreasonable for a v1 — a legitimate, deliberate choice, not an oversight this feature is passing on. Revisit if/when FEAT-01x's real UX needs prove otherwise.

**2026-07-24 self-audit — one real gap found and fixed, four confirmed clean:**
- [x] **Figure-fetch partial failure (real gap, fixed):** a genuine 404 from the real Storage backend for ONE of three figure chunks previously crashed `fetch_generator_chunks()` entirely uncaught — confirmed live, including that the other two valid figures were never even attempted (the loop died partway through). Fixed: each download is now caught individually; a failure degrades that one chunk to a text-only entry (`element_type` changed from `"figure"` — deliberately, so it never claims to have an image it doesn't, keeping `GeneratorChunk.__post_init__`'s existing guard meaningful rather than working around it) and logs a clear WARNING (`chunk_id`/`figure_path` only, never raw exception text). `test_figure_fetcher_degrades_one_failed_download_without_losing_the_others` locks this in against a real missing Storage object, not a mock.
- [x] **`user_id` trust boundary, adversarially (confirmed clean):** a MIXED `document_ids` list (caller's own document + a real other user's document) is rejected wholesale with 403 — `retrieve()` is never called at all (`fake_retriever.calls == []`), so there is no code path where a partial/mixed result could ever be computed or leaked. Meaningfully stronger proof than a wholly-other-user-id test alone.
- [x] **`document_id` fix, re-verified end-to-end (confirmed clean):** two real, different documents in one request (deliberately ordered opposite to citation order, to rule out a "defaults to the first `document_id` in the request" bug) — each citation's `document_id` correctly identifies which of the two documents it actually came from.
- [x] **Claim-span extraction, adversarial (confirmed safe, but a real precision limitation found):** two citations sharing one sentence with DIFFERENT verdicts (`"Revenue grew [1] due to strong sales in Q3 [2]."`, `[1]` supported / `[2]` unsupported) do not cross-contaminate the *response* — `[1]` survives with its marker intact, `[2]` is dropped and stripped cleanly. However, confirmed directly (not assumed): both citations are verified against the IDENTICAL full-sentence claim text, meaning each verdict is judged against content that includes the other citation's unrelated clause too. Not a safety bug (verdicts still diverged and stripped correctly) but a real quality/precision limitation of sentence-level granularity — documented in the test itself (`test_two_citations_in_one_sentence_with_different_verdicts_do_not_cross_contaminate`), not fixed (would need sub-sentence/clause-level extraction, judged out of scope for this pass).
- [x] **Timeout exposure, confirmed (inherits the known Phase 5 gap, and has one distinct exposure):** no timeout exists anywhere in the chain — confirmed via SDK source (`google/genai/_api_client.py` passes `timeout=None` explicitly per request when `HttpOptions.timeout` isn't set, and `httpx.Timeout(None)` means unlimited wait, not "use the client default"), confirmed via grep (no timeout config anywhere in `main.py`, middleware, or deployment config, which doesn't exist yet), and confirmed live (an 8-second delay inside a faked `generate()` call passed through the real endpoint completely untouched — no cancellation, no error). This is the same root-cause gap already logged in `.agent/SCOPE.md`'s Phase 5 "Production job execution" entry, which explicitly named `/query` (**"...and `/query` once built"**) — not a new, unscoped finding. `/query` does have one exposure distinct from `/ingest`'s: since `/query` runs synchronously in the request (a deliberate choice, see Decision above), a hung Gemini call blocks the calling client indefinitely, not just an invisible background task — worth noting precisely for whoever picks up the Phase 5 item, not fixed here.

**Run:**
- `curl -X POST -H "Authorization: Bearer <jwt>" -d '{"question":"...","document_ids":["..."]}' http://localhost:8000/query`

---

### [FEAT-026] `GET /conversations` + `GET /conversations/{id}/messages` + citation figure URLs
**Phase:** 2
**Status:** tested
**Owner:** claude-code
**Depends on:** FEAT-008 (list/detail/404 pattern), FEAT-012 (`/query`, citations, `create_query_turn`)
**Files:**
- `apps/api/routes/conversations.py` (new)
- `apps/api/routes/_pagination.py` (new — `encode_cursor`/`decode_cursor` extracted out of `documents.py`, now shared by both routers rather than duplicated a second time)
- `apps/api/models/conversations.py` (new)
- `apps/api/models/query.py` (`CitationResponse.figure_url` added)
- `apps/api/db/queries.py` (`list_conversations`, `get_conversation_detail`, `list_messages_for_conversation`, `list_citations_for_messages` added)
- `apps/api/services/figure_fetcher.py` (`signed_figure_url()` added — shared by `/query` and the new messages route, not duplicated)
- `apps/api/services/generator.py` (`GeneratorChunk.figure_path` added — carries `figure_fetcher.py`'s already-resolved figure path through to the citation-building step, avoiding a second `chunks.select("figure_path")` lookup)
- `apps/api/routes/query.py` (`marker` now persisted; `figure_url` populated for figure citations; `response_model_exclude_none=True` added)
- `apps/api/migrations/20260725_001_citation_marker_column.sql` (new)
- `apps/api/migrations/20260725_002_query_persistence_function_marker.sql` (new — `create_query_turn` now stores `marker`)
**Tests:**
- `apps/api/tests/test_conversations.py`
**Acceptance criteria:**
- [x] `GET /conversations` — paginated list, scoped to JWT user, matching FEAT-008's list pattern (keyset cursor on `updated_at desc`, `+1`-row has-more-page probe)
- [x] `GET /conversations/{id}/messages` — full message history including citations, matching API_CONTRACT.md's documented shape
- [x] Non-leaking 404 for another user's conversation (same status + body whether nonexistent or someone else's — FEAT-008's `get_document()` discipline, reused verbatim via `get_conversation_detail()`)
- [x] Citation figure URLs: signed, 600s-expiring Storage URL added to `element_type: "figure"` citations in both `POST /query` and the new messages route; omitted (not `null`) on non-figure citations

**Real gap found before either route could be built correctly: `citations` had no `marker` column at all.** `POST /query`'s response `marker` field (the inline `[N]` position) was purely ephemeral — computed in Python from `Generator`'s in-memory `cited_indices`, never part of `citations_to_persist`. This wasn't just a missing field: a message's stored `content` text keeps the *original*, non-renumbered `[N]` markers (`_strip_dropped_markers` removes dropped positions but never renumbers survivors), so reconstructing `marker` at read time by renumbering the kept citations sequentially (1, 2, 3...) would silently mismatch whatever `[N]` the stored text actually contains whenever an earlier citation had been dropped as unsupported. Confirmed this precisely by reading `create_query_turn`'s RPC body directly, not assumed from the model shape. Fixed at the source — a real column (`20260725_001`), the RPC updated to store it (`20260725_002`), `routes/query.py` passes `position` through — rather than a read-time workaround that would have been silently wrong in exactly the case (a dropped citation) that matters most for a citation-verification product.

**Verified empirically before writing the queries, not assumed:** PostgREST's embedded-count (`select=...,messages(count)`) and multi-level embed (`citations` → `chunks` → `documents`, three FK hops) syntax were both probed live against the real local stack with throwaway data before committing to the design — confirmed exact response shapes (`{"messages": [{"count": N}]}`, nested `chunks.documents.filename`), not guessed from PostgREST documentation.

**Decision — direct signed URL, not a cached/persisted one:** `figure_url` is built fresh on every read (live `/query` response or historical `/conversations/{id}/messages` read) from the citation's `chunk_id` → `figure_path`, never stored. A persisted URL would eventually be served already-expired; building it at read time means it's always valid for a fresh 600s window from the moment it's actually handed to a client. Confirmed live: the same figure citation produces two different (both genuinely fetchable) signed URLs when read once from `/query` and again later from `/conversations/{id}/messages`.

**Decision — FEAT number.** This was scoped verbally as a standalone task without an assigned number; `FEAT-016` through `FEAT-025` were already reserved in this file's Phase 4/5 placeholder lists (SSE streaming, OCR fallback, reranker, deploy tasks, etc.) before this work started — confirmed by reading this file first, not assumed free. Used `FEAT-026`, the next actually-unused number, and placed the entry in Phase 2 (immediately after FEAT-012) since this is a direct continuation of `/query`'s own conversation/citation model, not a Phase 4 polish item.

**Live-verified, real data throughout (`tests/test_conversations.py`):** a real `POST /query` call (real retrieval/generation/verification fakes, exactly `routes/query.py`'s own dependency-override pattern — never a hand-inserted conversation/message/citation row) persists a real conversation; `GET /conversations` lists it correctly with the right `message_count`; `GET /conversations/{id}/messages` returns the real message history with the real `marker` value (asserted equal to what `POST /query` itself returned, not just present); unsupported citations are correctly absent from history, matching what `POST /query` itself dropped; a real figure citation's `figure_url` — both the live one from `POST /query` and the one read back later from history — was fetched over real HTTP and its bytes compared against the real uploaded PNG.

**Run:**
- `curl -H "Authorization: Bearer <jwt>" http://localhost:8000/conversations`
- `curl -H "Authorization: Bearer <jwt>" http://localhost:8000/conversations/<id>/messages`

---

## Phase 3 — Frontend + Auth

### [FEAT-013] Next.js app shell + Supabase Auth
**Phase:** 3
**Status:** tested
**Owner:** claude-design (shell/UI) + claude-code (auth wiring)
**Files:**
- `apps/web/app/layout.tsx`
- `apps/web/app/(auth)/login/page.tsx` — real `signInWithPassword`, `signInWithOAuth("google")`, `resetPasswordForEmail`
- `apps/web/app/(auth)/signup/page.tsx` — real `signUp`, password-confirmation validation, handles both immediate-session and email-confirmation-required outcomes
- `apps/web/middleware.ts` — protects all `(app)/*` routes via `getClaims()`, redirects both directions ((un)authenticated ↔ `/login`/`/signup`); `/auth/*` exempted (see self-audit fixes below)
- `apps/web/lib/supabase/browser.ts` + `server.ts` — `@supabase/ssr` client/server clients
- `apps/web/components/layout/sidebar.tsx` (`onSignOut` affordance added — none existed pre-wiring; the reference screens this shell was translated from didn't show one)
- `apps/web/app/(app)/documents/page.tsx`, `apps/web/app/(app)/chat/[conversation_id]/page.tsx` (sign-out handler wired: `supabase.auth.signOut()` + redirect)
- `apps/web/app/auth/callback/route.ts` — PKCE code-exchange callback, added in the self-audit fix pass below
- `apps/web/app/account/update-password/page.tsx` — added in the self-audit fix pass below
**Tests:**
- `apps/web/e2e/auth.e2e.ts` (real `@playwright/test` suite — 8 tests, all against the local Supabase stack, none mocked)
- `apps/web/e2e/password-reset.e2e.ts` (3 tests, added in the self-audit fix pass below)
**Acceptance criteria:**
- [x] Login with email/password works
- [x] Signup creates a Supabase Auth user
- [x] Google OAuth flow works (verified the button initiates the real redirect to Google's/GoTrue's `/auth/v1/authorize` endpoint; completing external Google consent isn't automatable without a real Google account, so that leg is unverified by design, not by oversight — see self-audit item 1 and `.agent/GAPS.md`)
- [x] Protected routes redirect unauthenticated users to /login
- [x] Layout has navigation shell (sidebar + top bar)

**Decision — test runner:** `/test-scaffold` generated a jest-style `.e2e.ts` stub, but no test runner existed in `apps/web`. Added `@playwright/test` as a real, permanent dependency (`playwright.config.ts`, `testMatch: "**/*.e2e.ts"` to keep the scaffold's naming convention) rather than leaving an unusable stub — matches this project's real-infrastructure-testing discipline already established on the backend (`apps/api/tests/_local_supabase.py`); `apps/web/e2e/_local-supabase.ts` mirrors it for Node.

**Decision — env var naming:** current live Supabase docs (fetched 2026-07-24, see `.agent/api-docs/supabase.md`) use `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` terminology now. Kept this repo's existing `NEXT_PUBLIC_SUPABASE_ANON_KEY` name instead of renaming an already-wired convention — both key formats work identically as the client's `apikey` header; no functional difference.

**PRIORITY verification — real frontend JWT accepted by FEAT-003's backend middleware:** signed in through the real UI (Playwright, not mocked), captured the real session's `access_token` off the network response, decoded it (`alg: ES256`, `iss: http://127.0.0.1:54321/auth/v1`, `aud: authenticated`, `sub: <real user id>`), then called the live FastAPI dev server's `GET /documents` directly with it. Backend was pointed at the same local Supabase project via OS-env-var overrides (`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`, same `load_dotenv(override=False)` pattern `conftest.py` already used) — **200, `{"documents":[],"next_cursor":null}`**, the correct empty result for a brand-new user, proving both JWT acceptance and `user_id` scoping. This is the first time a real browser-issued token has been checked against FEAT-003's JWKS/ES256 middleware; confirmed, not assumed from separate correctness.

**Verified live, local Supabase stack only (never production):** real signup creates a real, queryable local Auth user; real login with those credentials succeeds; unauthenticated → `/login` and authenticated-hitting-`/login` → `/documents` both redirect correctly (`middleware.ts`'s `getClaims()` branch, not just the client-side post-login `router.push`); sign-out terminates the session (`supabase.auth.signOut()`) such that a subsequent request to `/documents` redirects again. Visual regression: `/login` and `/signup` re-screenshotted post-wiring (light, dark, mobile, and a real invalid-credentials error state) — no layout disturbance from the new form-state/error/loading logic.

**Run:** (from `apps/web`)
- `pnpm dev` (reads `.env.development.local` for the local Supabase stack in dev mode, ahead of `.env.local`'s production values)
- `pnpm e2e` (requires `supabase start` first)

**2026-07-24/25 self-audit fixes — three confirmed dead ends, all fixed and live-verified:**

A dedicated self-audit (same day as the wiring above) found two confirmed dead ends sharing one
root cause, plus one real middleware bug:

- [x] **Item 1 — no OAuth/PKCE callback route existed at all.** `signInWithOAuth("google")`
  pointed `redirectTo` straight at `/documents`, never exchanging the returned `?code=` for a
  session (`@supabase/ssr`'s browser client hardcodes `flowType: "pkce"`, confirmed via installed
  source). **Fix:** `app/auth/callback/route.ts` — `exchangeCodeForSession(code)`, routes to
  `next` (query param) on success, `/login?error=...` on failure. Cannot be fully live-tested:
  the local Supabase project has no Google client configured
  (`.../authorize?provider=google` → `"Unsupported provider"`), and completing Google's real
  consent screen isn't automatable regardless — see `.agent/GAPS.md` and `.agent/MEMORY.md`'s
  §Open questions for the manual verification checklist to run once a real client exists. The
  shared PKCE mechanism itself IS fully proven, via item 2's live end-to-end test.
- [x] **Item 2 — password reset had no completion page; a valid recovery code landed inertly on
  the ordinary login form.** Same callback route fixes this (recovery links pass
  `next=/account/update-password`). New `app/account/update-password/page.tsx` calls
  `supabase.auth.updateUser({ password })`. **Live-verified fully end-to-end**
  (`e2e/password-reset.e2e.ts`): real signup → real `resetPasswordForEmail` → real email
  retrieved from the local Mailpit mail catcher (`INBUCKET_URL`/`MAILPIT_URL` point at the same
  host — only Mailpit's own API works, not classic Inbucket's) → real link clicked → real PKCE
  exchange → real password set → old password rejected, new password succeeds a completely
  separate fresh login.
  **Bonus bug found and fixed while verifying this live:** Next.js 14.2.35's dev server reports
  `new URL(request.url).origin` as `http://localhost:3000` inside a Route Handler regardless of
  which host the client actually connected with (confirmed via direct `curl` against
  `127.0.0.1:3000`) — since GoTrue's redirect allowlist (`config.toml`'s `site_url`) is pinned to
  `127.0.0.1:3000`, this silently broke the whole flow (browser lands on a different origin than
  the one holding the session cookie). Fixed by preferring the real `Host`/`x-forwarded-host`
  header over `request.url`'s own origin (`resolveOrigin()` in the callback route) — the official
  Supabase example code doesn't account for this. Also required pinning
  `playwright.config.ts`'s `baseURL`/`webServer.url` to `127.0.0.1:3000` (was `localhost:3000`) to
  match. Full trace evidence and the exact fix are in `.agent/api-docs/supabase.md`.
- [x] **Item 4 — middleware dropped refreshed session cookies on both redirect branches.**
  `NextResponse.redirect(url)` was constructed fresh in both branches, structurally disconnected
  from the `response` object `setAll` had mutated with any refreshed session cookies — a gotcha
  this project's own doc cache had already flagged from the original wiring pass but the code
  didn't follow. **Fix:** both branches now copy `response.cookies.getAll()` onto the redirect
  response before returning (Next.js's `ResponseCookies` has no bulk `setAll` — the official
  fetched pattern's exact method name didn't type-check; ground truth came from `tsc`, not the
  doc summary — see `.agent/api-docs/supabase.md`). **Live A/B-verified, not just code-reviewed:**
  temporarily set local `jwt_expiry` to 10s, signed in, waited past expiry, then hit `/login`
  (the affected branch) with the now-expired-but-refresh-eligible session cookie via a raw
  `fetch` — inspected the real `Set-Cookie` response headers directly. Buggy code: `0` Set-Cookie
  headers (session silently dropped). Fixed code: `1` Set-Cookie header carrying a genuinely
  rotated token value (confirmed different from the pre-wait cookie). Reverted `jwt_expiry` and
  restarted the local stack (reapplied all four migrations) afterward.
- [x] **Item 3 — login page silently rendered as normal for a stray/expired `?code=` or
  `?error=`.** Now reads both via `useSearchParams()` (wrapped in `Suspense`, required by
  Next.js) and shows a real message instead of a bare sign-in form — live-verified for both the
  callback route's own forwarded error and a directly-bookmarked stale `?code=`
  (`password-reset.e2e.ts`'s second and third tests).

**Full suite after the fix pass: 10/10 e2e tests passing** (8 from the original wiring pass + 2
new full-flow tests, run single-worker — `fullyParallel: false` alone wasn't enough once a second
test file existed; concurrent workers contending for the same dev server/local Supabase instance
caused a real timeout unrelated to the fix itself, see `playwright.config.ts`'s `workers: 1`).
`tsc --noEmit` clean.

---

### [FEAT-014] Upload UI + document list
**Phase:** 3
**Status:** tested
**Owner:** claude-design (shell/UI, prior pass) + claude-code (real API wiring, this pass)
**Files:**
- `apps/web/app/(app)/documents/page.tsx` — real `GET /documents` on load, generation-guarded polling (see race-condition fix below), real delete wiring, distinct loading/error/empty states
- `apps/web/components/documents/upload-zone.tsx` — real direct-to-Storage upload + `POST /ingest`, honest indeterminate progress (no fabricated percentage — see Decision below)
- `apps/web/components/documents/document-card.tsx` — unchanged; already purely presentational, no mocked internals to wire
- `apps/web/components/documents/delete-confirm-dialog.tsx` — `error`/`deleting` props added for real 409/`STORAGE_ERROR` display
- `apps/web/lib/api/documents.ts` (new) — `listDocuments`, `getDocument`, `deleteDocument`, `uploadDocument`, shared `apiFetch` with real `ApiError`/401 handling
- `apps/web/lib/status-styles.ts` — verified unchanged (see below)
- `apps/web/tailwind.config.ts` — `indeterminate`/`indeterminate-bar` keyframe+animation added for the honest upload-progress state
- `apps/api/main.py` — **CORS middleware added; none existed at all** (see Gap below)
**Tests:**
- `apps/web/e2e/upload.e2e.ts` (real `@playwright/test` suite — 6 tests, all against the local Supabase stack + a live local FastAPI backend, none mocked)
**Acceptance criteria:**
- [x] Drag-drop or click-to-select PDF (click-path exercised in tests; both funnel through the same `handleFiles`)
- [x] Upload progress bar — honest indeterminate state, not a fabricated percentage (see Decision)
- [x] Document appears in list immediately with the **real** status, which is `'Uploaded'`, not `'parsing'` — the acceptance criterion's own wording was stale, matching the exact same correction API_CONTRACT.md already documented for FEAT-007 (an earlier draft example wrongly showed `"status": "parsing"`; the endpoint's 202 response always reflects the literal just-inserted `'uploaded'` row). Verified against real SCHEMA.md enum and live response, not assumed from the criterion text.
- [x] Status updates via polling — implemented as 2s-base exponential backoff (×1.5, capped at 60s), never permanently gives up as long as any document is non-terminal (see Decision on interpreting "initial: every 2s... max 60s")
- [x] Delete confirmation modal — real `DELETE /documents/{id}`, real 409 (`"still being parsed"`) and `STORAGE_ERROR` (`"safe to retry"`) copy, no raw error dump
- [x] Empty state when no docs
- [x] Error state when upload fails (client-side non-PDF rejection, real and tested; server-side pipeline failure confirmed live but not as a permanent CI test — see item 8 verification below)

**Verified against real backend source, not assumed:** `lib/status-styles.ts`'s 5-status `DOCUMENT_STATUS_STYLES` map (`uploaded | parsing | embedded | ready | failed`) was checked directly against `SCHEMA.md`'s `document_status` enum and `routes/documents.py`'s own `_VALID_STATUSES` set — exact match, no fix needed. The earlier addition of `embedded` (ahead of the original 4-status mock) guessed correctly.

**Decision — direct browser-to-backend calls, not a Next.js API proxy layer:** ARCHITECTURE.md's ingest-flow diagram describes a `Next.js API route /api/ingest` intermediary that validates the JWT and forwards to FastAPI. That layer was never built (`app/api/` is still an empty `.gitkeep`) and doesn't match what FEAT-013 actually wired (`NEXT_PUBLIC_API_URL`, a public env var, plus a direct `Authorization: Bearer` pattern already proven end-to-end in FEAT-013's priority JWT cross-stack check). Followed the real, already-proven pattern instead of the stale diagram — `lib/api/documents.ts` calls FastAPI directly from the browser.

**Decision — honest indeterminate upload progress, not a fabricated percentage:** confirmed via the installed `@supabase/storage-js` source (`StorageFileApi.ts`) that `upload()` is fetch-based internally with no `onUploadProgress` option anywhere in its type signature or implementation — there is no real byte-level progress available. The prior mock's fake interval-driven percentage was actively misleading (implying real progress data that doesn't exist). Replaced with an indeterminate sliding-bar animation (`indeterminate-bar`, reusing the existing design system's animation-token pattern) and "UPLOADING…" text instead.

**Decision — polling backoff interpretation:** the acceptance criterion "initial: every 2s while not ready/failed, max 60s" is ambiguous between "poll for at most 60s total then give up" and "back off up to a 60s ceiling, never giving up." Implemented the latter — a hard cutoff would permanently strand the UI on a stale status for any document whose real pipeline genuinely takes longer than 60s (confirmed this is a real, not just hypothetical, scenario — see the `table_heavy.pdf` verification below, which took several minutes under real Voyage rate-limit backoff and would have been abandoned by a hard 60s cutoff despite completing successfully).

**Real gap found and fixed — no CORS middleware existed on the backend at all.** Confirmed via `Grep` across all of `apps/api`: zero matches for CORS/`allow_origin` anywhere. Every real cross-origin `fetch` from the browser (a different origin than the backend, always, regardless of hostname) would have been blocked before ever reaching a route — this would have made the entire wiring pass impossible to verify live, not just an edge case. Added `CORSMiddleware` to `apps/api/main.py`, ordered **after** `JWTAuthMiddleware` (Starlette wraps middlewares in reverse registration order — the last added is outermost and runs first, which is required so the browser's credential-less `OPTIONS` preflight is handled before `JWTAuthMiddleware` would otherwise reject it with 401). Confirmed live: `curl -X OPTIONS` preflight against `/documents` returns `200` with correct `Access-Control-Allow-*` headers and critically no 401.

**Real bug found live and fixed — a stale in-flight poll response could resurrect a just-deleted document.** While live-testing the delete flow, a genuinely deleted document (confirmed via a real `204` response) reappeared in the UI moments later. Root cause, confirmed via network-response timestamps: `pollTick`'s `GET /documents` can still be in flight when the user deletes a document; if that stale response is applied after the fact via a bare `setDocs(result.documents)`, it silently overwrites the newer (post-delete) state with the pre-delete snapshot. Fixed with a generation counter (`docsGenerationRef`) — every authoritative docs update (`commitDocs`, used by initial load, retry, upload, delete) bumps it; `pollTick` captures the generation before its own network call and discards its result if the generation moved on while it was in flight. Live-reproduced with the buggy version reverted temporarily for contrast: without the guard, the deleted document visibly reappeared; with it, deletion holds.

**PRIORITY verification (item 8) — real PDF through the real pipeline via the real UI, proven twice:**
- `clean_digital.pdf` (small, fast): fully proven as a **permanent, passing e2e test** (`upload.e2e.ts`) — real upload → real status transitions observed via real polling (not a reload) → real `Ready` → real delete → confirmed gone, including after a page reload (rules out optimistic-only local state).
- `table_heavy.pdf` (the fixture this task explicitly named): uploaded via the real UI, real Docling parse, hit a **real external constraint** — Voyage API's free-tier rate limit (`3 RPM`), exhausted from this session's cumulative testing — triggering the embedder's real retry-with-backoff path (`services/embedder.py`'s `MAX_RETRIES`). Took several minutes under that backoff, well past a naive 60s ceiling, but the document **did reach `ready`**, confirmed directly against the database (`status: ready`) after the UI's own polling window had been given up on by the test script (not by the app itself — the app's own uncapped backoff was still correctly polling). This is exactly why the "never give up" polling interpretation above was the right call, discovered empirically rather than assumed. Cleaned up the resulting test document afterward.
- Also surfaced (not previously logged anywhere — checked `.agent/GAPS.md` before writing this, this is a new finding, not a re-confirmation) a real delete-vs-in-flight-pipeline race: a `chunks_document_id_fkey` violation when a document row is removed while its background task is still writing chunks. `DELETE /documents/{id}`'s `409` check only blocks `status == 'parsing'` — a document in `'embedded'` also still has an in-flight background task (figure upload + chunk insert + `mark_ready` haven't run yet) but isn't blocked. Surfaced here by this session's own aggressive test-cleanup (deleting a test user, which cascades to their documents via FK, while a pipeline was still mid-flight) — not exploitable the same way by a real user deleting their own single document through the normal UI (no cascade-via-user-deletion path is exposed there), but the underlying race is real and worth a dedicated look. Logged properly in `.agent/GAPS.md` rather than left as a one-line aside — out of scope to fix in this wiring-correctness pass.

**Multi-tenant sanity check (item 9):** two real users, real separate browser contexts — user B (uploaded nothing) never sees user A's document, confirmed both before and after a reload. Structurally guaranteed by FEAT-008's `user_id`-scoped queries; this is the UI-layer confirmation.

**401 mid-session verification — real, live-proven, not assumed.** This took significant live investigation to reproduce correctly: the Supabase JS client transparently auto-refreshes near-expiry access tokens inside `getSession()` (confirmed via the installed `@supabase/auth-js` source), and additionally preserves a still-real-time-valid session even after a *failed* refresh attempt (`__loadSession()`'s `accessTokenStillValid` fallback, also confirmed via source) — so a merely-expired access token alone never actually reaches the UI as a dead session in normal use. A genuinely dead session requires the refresh token itself to be invalid AND the access token's real (not just client-side-cached) expiry to have passed. Reproduced live by corrupting only the stored `refresh_token` (leaving the real, signed `access_token` untouched, avoiding both the "caught by `middleware.ts` first" false path from a corrupted signature and the "cascades away the test document" false path from deleting the underlying user) and waiting past real expiry: confirmed via console instrumentation that `getSession()` correctly returned `session: null`, `getAccessToken()` correctly detected it and called `forceReauth()`, `signOut()` resolved cleanly, and the browser genuinely navigated to `/login`. Debug instrumentation removed after confirming; not kept as a permanent test (requires temporarily shortening `jwt_expiry`, the same protocol already established in FEAT-013's middleware audit).

**Full suite: 16/16 e2e tests passing** (7 auth + 3 password-reset + 6 upload/documents), single-worker (established in FEAT-013's audit — concurrent workers against the same real dev server/local Supabase instance caused real, non-representative timeouts). `tsc --noEmit` clean.

**Run:** (from `apps/web`, with `apps/api`'s local-stack-pointed `uvicorn` also running — see FEAT-013's entry for the exact env-var-override invocation)
- `pnpm dev`
- `pnpm e2e` (requires `supabase start` and the backend both running first)

---

### [FEAT-015] Chat UI + citation source panel
**Phase:** 3
**Status:** tested — UI translation (2026-07-25) + real wiring (2026-07-25) both complete
**Owner:** claude-design (UI), claude-code (wiring)
**Files:**
- `apps/web/app/(app)/chat/[conversation_id]/page.tsx`
- `apps/web/app/(app)/chat/page.tsx` — conversation list (new; see placeholder note below)
- `apps/web/components/chat/message-bubble.tsx`
- `apps/web/components/chat/citation-marker.tsx` (renamed from the originally-planned `citation-chip.tsx` during the UI translation pass)
- `apps/web/components/chat/source-panel.tsx`
- `apps/web/components/chat/question-input.tsx`
- `apps/web/components/chat/loading-stages.tsx`
- `apps/web/components/documents/document-card.tsx` — added optional selection checkbox
- `apps/web/app/(app)/documents/page.tsx` — added selection state + "Ask about these" bar (placeholder, see below)
- `apps/web/lib/api/client.ts` (new) — shared `apiFetch`/`ApiError`/token-refresh, extracted from `lib/api/documents.ts` when `query.ts`/`conversations.ts` needed the identical logic a second and third time
- `apps/web/lib/api/query.ts` (new) — real `POST /query`
- `apps/web/lib/api/conversations.ts` (new) — real `GET /conversations`, `GET /conversations/{id}/messages`
- `apps/web/lib/api/types.ts` (new) — shared `ApiCitation` wire type
- `apps/web/lib/chat/parse-message.ts` (new) — shared `[N]`-marker → citation segment parser, used by both `query.ts` and `conversations.ts` so a live and a reloaded answer can never parse differently
- `apps/web/lib/types/chat.ts` — added `Citation.figureUrl`
**Tests:**
- `apps/web/e2e/chat.e2e.ts` — 3 real Playwright tests (one full real Docling/Voyage/Gemini pipeline run through the actual UI, two seeded via the real `create_query_turn` RPC for deterministic citation/figure/isolation assertions)
- `apps/web/e2e/_seed.ts` (new) — direct-REST seeding helpers (documents/chunks/figures/conversation turns), mirroring `test_conversations.py`'s own seeding pattern on the Python side
**Acceptance criteria:**
- [x] Question input at bottom, messages scroll above
- [x] Assistant messages render inline citation markers
- [x] Click citation → source panel opens with chunk content + page image (if figure)
- [x] Verdict-based citation styling (supported = solid, partial = dashed warning, no unsupported shown)
- [x] Loading state during retrieval + generation
- [x] Auto-scroll to newest message
- [x] Mobile-responsive (source panel becomes a full-width overlay below `sm`; not a literal bottom sheet — see note)

**Real wiring pass (2026-07-25), replacing all mocked handlers/data:**

**Real bug found and fixed, only visible in an actual browser — not from reading the code:** every citation marker was genuinely unclickable. Tailwind's Preflight reset sets `sup { line-height: 0 }` (the standard typographic sub/sup reset) and separately resets `button { line-height: inherit }` — so `citation-marker.tsx`'s `<button>`, nested inside a `<sup>`, inherited a computed `line-height: 0` and collapsed to zero height (`getComputedStyle` confirmed `height: 0px` exactly). This is invisible in a static screenshot or in the original (non-Tailwind) design-tool mockup, and would have shipped silently — Playwright's real click-actionability check (`element is not visible`, retried for 60s) is what caught it, not a visual review. Fixed with an explicit `leading-[1.4]` directly on the button, which overrides the inherited value (inheritance always loses to any rule matching the element itself, regardless of specificity). Confirms this project's "use the feature in a browser before calling it done" discipline is load-bearing, not procedural — this bug would have passed every prior check (typecheck, unit-level reasoning, a static screenshot) and only surfaced under real interaction.

**Citation marker parsing, shared not duplicated:** `content`/`answer` text contains literal `[N]` markers (possibly grouped — `services/generator.py`'s own comment notes Gemini has been observed emitting `[2, 3]`); a bracket with no matching citation (a hallucinated marker `_strip_dropped_markers` doesn't touch, since only *dropped-but-otherwise-valid* positions are stripped) renders as inert plain text rather than crashing. One parser (`lib/chat/parse-message.ts`) is shared by both `POST /query`'s live response and `GET /conversations/{id}/messages`' historical read, so the two can never silently disagree on how a marker maps to a citation.

**Marker persistence proven at the UI layer, not just the API layer:** the real end-to-end e2e test asks a real question, records the rendered citation marker numbers, reloads the page (forcing a real `GET /conversations/{id}/messages` fetch), and asserts the markers are byte-identical — the one place FEAT-026's marker-persistence fix (persisting `marker` at write time rather than re-deriving it at read time) is actually exercised by a real user action, not just a backend test.

**Two explicit placeholders, not a full Claude Design pass (flagged per this task's own instruction, not silently shipped as "done"):**
1. `apps/web/app/(app)/chat/page.tsx` — a plain conversation list (title, message count, updated-at) for the sidebar's already-existing `/chat` nav link. No pagination UI, no search/filter/delete/rename.
2. `apps/web/app/(app)/documents/page.tsx`'s selection checkboxes + floating "Ask about these" bar — a native `<button role of a styled div>` checkbox, not shadcn's Radix-based `Checkbox` (no `@radix-ui/react-checkbox` dependency added for this), loosely matching tokens.

Both are real, functionally complete, and covered by the e2e suite — just not run through a dedicated design pass. Revisit if/when chat gets its own UI iteration.

**figure_url end-to-end, real bytes:** the seeded e2e test uploads a real PNG to the real (local) `figures` Storage bucket, then fetches the rendered `<img>`'s `src` over real HTTP and compares bytes exactly against the source PNG — not just checking a URL-shaped string is present.

**Excerpt fallback:** `supporting_quote` is `null` for every figure citation (chunks have no text to quote verbatim) and can also be `null` for a `partial` verdict — `source-panel.tsx` falls back to the chunk's `snippet` rather than rendering an empty blockquote; covered by a real (seeded, deterministic) test.

**Verification:** `pnpm e2e -- e2e/chat.e2e.ts` — 3/3 passing (one real full-pipeline run ~22s, two seeded ~3-8s each). Re-ran `pnpm e2e -- e2e/upload.e2e.ts` (FEAT-014) afterward — still 6/6 passing, confirming the `document-card.tsx`/`documents/page.tsx` selection-checkbox additions caused no regression. Full `tsc --noEmit` clean throughout.

---

## Phase 4 — Polish (see SCOPE.md for full list)

Features here are placeholders until Phase 3 ships. Phase 3 shipped 2026-07-25 (FEAT-013/014/015,
closed out with a full real end-to-end browser pass — see FEAT-015's entry and that day's
CHANGELOG "Phase 3 close-out" entry) — FEAT-017 below is the first Phase 4 feature actually
started.

- [FEAT-016] Streaming responses via SSE — planned

### [FEAT-017] OCR fallback via Gemini Flash, extended to a 3-tier chain (Gemini -> OCR.space -> Tesseract)
**Phase:** 4
**Status:** tested
**Owner:** claude-code
**Files:**
- `apps/api/services/parser.py` — `GeminiOcrClient` (tier 1), `OcrSpaceClient` (tier 2, new), `TesseractOcrClient` (tier 3, new), `OCR_MODEL`, `OCR_SYSTEM_PROMPT`, `OCR_SPACE_URL`, `OCR_SPACE_ENGINE`, `_TEXTUAL_ELEMENT_TYPES`; the tier-walking loop lives directly in `Parser.parse()`. `Parser.__init__`'s param renamed `ocr_client` → `ocr_tiers: list[tuple[str, object]] | None = None`, defaulting to the real 3-tier chain — same pattern as the pre-existing `converter` param
- `apps/api/pyproject.toml` — `pytesseract` added (thin wrapper; the real `tesseract-ocr` system binary is Docker-only, see ARCHITECTURE.md)
- `apps/api/.env.example` — `OCR_SPACE_API_KEY`, `TESSERACT_CMD` (optional, local-dev-only path override)
- `.agent/api-docs/ocrspace.md` (new) — real REST shape, verified live, not from docs prose alone
**Tests:**
- `apps/api/tests/test_parser.py` — 23 tests total (up from 18): all `ocr_client=` call sites updated to `ocr_tiers=[("gemini", ...)]`; 4 new tests are the full deterministic combination matrix (tier 1 succeeds / tier 1 fails+tier 2 succeeds / tiers 1+2 fail+tier 3 succeeds / all three fail); 1 new real test forces tier 1 to fail and confirms real tier-2 (OCR.space) recovery plus an honest 3-way quality comparison against real Tesseract and real Gemini on the identical page image
**Acceptance criteria:**
- [x] Trigger is a positive heuristic over already-extracted elements (zero text-bearing elements on a page), never a Docling confidence score (none exists — FEAT-004 finding)
- [x] OCR fires per low-yield page using that page's own rendered image, not the whole document
- [x] Each tier only attempted if every prior tier failed (exception or no usable text) — never in parallel, never speculatively — confirmed via call-counting fakes across all 4 real combinations
- [x] OCR-recovered text becomes an ordinary `ParsedElement` (`element_type=TEXT`) in the same `elements` list `chunker.py` already consumes, regardless of which tier recovered it — no parallel data shape — confirmed by re-running the full `test_chunker.py` suite (21/21 passing) with zero test changes needed
- [x] Which tier actually recovered each page is logged (`gemini | ocrspace | tesseract | none`) — established as necessary for debugging retrieval quality
- [x] `scanned.pdf`: real recovered content reported for every previously-zero-element page (real output required, not a pass/fail assertion per the task brief) — see real output below
- [x] `clean_digital.pdf` and `table_heavy.pdf` (already high-yield): zero OCR calls across all three tiers — no regression in API call volume for normal digital PDFs
- [x] A tier's call failure degrades gracefully to the next tier (logged, no crash); all three failing leaves the page unrecovered, never taking down the rest of the parse

**Design decision — integrated inside `Parser.parse()`, not a separate post-processing pass.** A separate pass would need the original page images to send to Gemini; `ParsedDocument` doesn't carry those today (only `FIGURE` elements carry cropped images), so a separate pass would either re-run Docling's entire (expensive, 60-120s) conversion a second time just to get page images, or `ParsedDocument` would need a new field exposing raw page images to the outside world for a need nothing else has. Doing it inline reuses the exact same `converter.convert()` call already producing `doc.pages[n].image` (with `generate_page_images=True` added to the pipeline options) and lets OCR-recovered elements get appended to the same `elements` list before `ParsedDocument` is even constructed — `chunker.py` sees zero difference between Docling-native and OCR-recovered text by construction, not by a compatibility-mapping step. Matches the existing DI pattern (`Parser.__init__(converter=...)` already injectable; the Gemini OCR client is too, same reasoning STANDARDS.md gives for constructor-injected dependencies).

**Design decision — trigger heuristic deviates from the plan's own suggested example, based on real fixture evidence.** The original scoping note (above, now superseded) offered two illustrative options: a relative element-count threshold, or "zero elements on a page that isn't the document's last page." Implemented as: zero elements of type `TEXT`/`HEADING`/`TABLE`/`LIST` on a page (a lone `FIGURE` doesn't count as "the page has content" — a stray image with no caption/text is still consistent with an unread scanned page). The "not the last page" exemption from the original note is deliberately **not** implemented: `scanned.pdf`'s actual last page (page 3) is itself unread scanned content, not a genuine blank page — exempting it would leave real recoverable content silently unrecovered on exactly the document shape this feature exists for. A wasted OCR call on a genuinely blank last page (rare, low cost, free-tier-covered) is a better failure mode than silently repeating the exact data-loss bug FEAT-004 found.

**Design decision — applies to new ingests only; existing documents are not automatically reprocessed.** OCR fallback lives entirely inside `Parser.parse()`, which only ever runs during the ingest pipeline for a document actively being uploaded (`routes/ingest.py`'s `parser or Parser()`) — there is no scheduled or triggered re-parse of an already-`ready` document anywhere in this codebase. A document ingested before this feature existed keeps whatever chunks it already has; benefiting from OCR fallback after the fact would require re-fetching its original bytes from Storage and re-running the full ingest pipeline, which is exactly the job of `POST /reindex/{document_id}` — already listed in `API_CONTRACT.md`'s "Not-yet-defined endpoints" as planned, Phase 4+, currently unbuilt. Building that reindex mechanism (safe chunk replacement, avoiding orphaned citations pointing at deleted chunks) is real, separate work, out of scope here — stated explicitly rather than left implicit, per the task brief.

**Real recovered content (`scanned.pdf`, real Gemini call, 2026-07-25) — quoted, not paraphrased:**
- Page 1: unchanged — Docling's own real embedded heading ("SAMPLE LETTER") + the uncaptioned logo figure. **Zero OCR calls on this page** (it already has textual content — the trigger correctly does not fire here).
- Page 2 (previously 0 elements): *"o the materials inventory that systems were required to complete under the LCR, including the locations of lead service lines and lead plumbing in the system; and o LCR compliance sampling results collected by the system, as well as justifications for invalidation of LCR samples; and (5) Enhance ef[fforts...]"*
- Page 3 (previously 0 elements, and the document's actual last page — see the trigger-heuristic decision above): *"Thank you in advance for your support to ensure that we are fulfilling our joint responsibility for the protection of public health and to restore public confidence in our shared work to ensure safe drinking water for the American people. Sincerely, Joel Beauvais, Deputy Assistant Administrator, En[closure...]"*

This is a real EPA letter about lead service line compliance — confirms both the heuristic (only genuinely-empty pages triggered) and the last-page decision (page 3, the last page, needed and got OCR, which the original suggested heuristic would have skipped).

---

**2026-07-26 follow-up — extended to a 3-tier resilience chain (OCR.space, Tesseract).** Gemini alone has a real, already-hit constraint (`.agent/MEMORY.md`'s 2026-07-26 entry: 20 requests/day, per-model, free tier) — a single-vendor OCR path can go down for a whole day from ordinary development/testing volume alone, let alone a real outage. Added two more tiers, each only attempted if every prior one fails:

**Chain order, as specified:** Gemini (tier 1) → OCR.space (tier 2, independent vendor — a Gemini-side outage or quota exhaustion has zero chance of also taking out a different company's infrastructure) → Tesseract (tier 3, self-hosted, no network call, no vendor quota of any kind — the true last resort) → unrecovered (existing fail-safe unchanged).

**OCR.space verified live before coding, not assumed** (`.agent/api-docs/ocrspace.md`, new) — a plain REST endpoint, no SDK: `POST https://api.ocr.space/parse/image`, `apikey` sent as a **header** (not a body/query param — easy to get wrong), image sent as `base64Image`, recognized text at `ParsedResults[0]["ParsedText"]`, and critically: a processing failure comes back as a normal 200 OK with `IsErroredOnProcessing: true` — `raise_for_status()` alone would miss it. The public `helloworld` demo key (500 req/day/IP, no registration) is real and was used for all live testing here — confirmed working via a direct call before any code was written.

**Tesseract required real local installation to test for real** (not just faked) — `pytesseract` alone is a thin wrapper with nothing to wrap without the system `tesseract-ocr` binary. `choco install tesseract` failed in this sandbox (no admin rights to write chocolatey's lock directory — a real, different-cause echo of the exact "can't install system packages outside a real container" constraint the ARCHITECTURE.md deploy note is about); `winget install UB-Mannheim.TesseractOCR` succeeded, enabling a genuine local real-Tesseract test rather than relying on a fake for tier 3 entirely.

**Interface design:** every tier shares one contract, `transcribe_page(image) -> str | None` — identical to `GeminiOcrClient`'s pre-existing shape, so `OcrSpaceClient`/`TesseractOcrClient`/test fakes are all interchangeable list entries. `Parser.parse()`'s per-page loop walks `self._ocr_tiers` (a `list[tuple[name, client]]`) directly — no wrapping "chain" object — since tier-name logging needs `page_number`, which already lives in that exact loop scope; wrapping the chain in its own class would have meant inventing a new return-type contract (`(text, tier_name)`) instead of reusing the simple one every individual client already has.

**Real 3-way quality comparison, same page image, honestly reported (task brief item 7):**
- **OCR.space (real, via the chain, tier 1 forced to fail):** *"...the materials inventory that systems were required to complete under the LCR, including the locations of lead service lines, together with any more updated inventory or map of lead service lines and lead plumbing in the system; and LCR compliance sampling results collected by the system, as well as justifications for invalidation of LCR samples; and (5) Enhance efforts to ensure that residents..."* — clean, correct punctuation, no visible OCR noise.
- **Tesseract (real, local binary, same image):** *"(...the materials inventory that systems were required to complete under the LCR, including the locations of lead service lines, together with any more updated inventory or map of | lead service lines and lead plumbing in the system; and  ...LCR compliance sampling results collected by the system, as well as justifications for invalidation of LCR samples, **and** (5) Enhance efforts to ensure that res[idents]..."* — same core content, fully usable, but with real, visible OCR-engine artifacts: a stray leading `(`, a stray `|` character mid-line, extra blank lines, and a semicolon→comma slip ("samples; and" → "samples, and"). Weaker than OCR.space, not broken.
- **Gemini (tier 1):** unavailable for a fresh real call today — the same 2026-07-26 quota exhaustion logged in MEMORY.md, confirmed still in effect (real `429`, not assumed). From this feature's own earlier-in-session real capture (before quota ran out, same page): *"o the materials inventory that systems were required to complete under the LCR, including the locations of lead service lines and lead plumbing in the system; and..."* — no visible artifacts, the cleanest of the three, consistent with a modern vision-LLM generally outperforming a classical OCR engine on real-world scan quality.
**Honest ranking on this one real page: Gemini ≥ OCR.space > Tesseract** — all three genuinely usable, Tesseract measurably noisier. This is exactly the ordering the chain's own tier order assumes (best tier tried first), now backed by a real same-page comparison rather than assumed from each vendor's reputation.

**Known gap, stated not hidden: `uv.lock` was not regenerated.** `pytesseract` was installed directly into the local `.venv` via `pip` (bootstrapped via `ensurepip`, since this dev sandbox has no `uv` binary on PATH) to enable real testing — `pyproject.toml` correctly declares the new dependency, but `uv.lock` still doesn't reflect it. Hand-editing a lock file's hashes/resolution metadata was judged too risky to attempt blind; whoever next runs `uv lock` in an environment where `uv` is actually available should expect it to pick up this new dependency for the first time.

**Verification:** `test_parser.py` 23/23 passing — 21 deterministic (real Docling load only, no network calls, ~10 min) confirmed clean first; the 2 real-API tests run separately afterward (~30s), both passing, with real recovered text and the real 3-way comparison above. `test_chunker.py` re-run, 21/21, still no regression. Full backend suite re-run for final regression confirmation (see CHANGELOG).

- [FEAT-018] Reranker step — planned
- [FEAT-019] Conversation memory in prompt — planned
- [FEAT-020] DOCX/PPTX ingestion — planned

---

## Phase 5 — Deploy (see SCOPE.md for full list)

- [FEAT-021] Vercel prod deploy — planned
- [FEAT-022] Render prod deploy — planned
- [FEAT-023] Landing page + demo — planned
- [FEAT-024] Rate limiting — planned
- [FEAT-025] Error tracking (Sentry free) — planned

---

## Considered Suggestions

Promoted from HANDOFF.md `## Agent Suggestions` inbox after review. Not yet scheduled into a phase.

- **Embedder process-lifetime singleton** — safe per `.agent/reviews/2026-07-23-efficiency.md`'s concrete thread-safety assessment (tokenizer cache, Rust `tokenizers` backend, HTTP client all confirmed safe for concurrent use). Low risk, real win. Independent of Phase 5.
- **Parser/DocumentConverter process-lifetime singleton + lock around `.parse()`** — captures the expensive model-loading reuse while sidestepping Docling's own unvalidated concurrent-`execute()` caveat (see the same review). Independent of Phase 5.
- Both currently unimplemented — pick up opportunistically if/when this area is next touched, not urgent standalone work.
