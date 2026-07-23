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
**Status:** planned
**Owner:** claude-code
**Depends on:** FEAT-003, FEAT-004, FEAT-005, FEAT-006
**Files:**
- `apps/api/routes/ingest.py`
- `apps/api/db/queries.py` — document + chunk insert helpers
**Tests:**
- `apps/api/tests/test_ingest.py` — integration test with local Supabase
- `apps/api/tests/e2e/test_ingest_e2e.py`
**Acceptance criteria:**
- [ ] POST /ingest with valid body returns 202 + document_id
- [ ] storage_path prefix mismatch with JWT user_id returns 403
- [ ] Background task: download → parse → chunk → embed → insert → update status
- [ ] Failure at any stage sets documents.status = 'failed' with error message
- [ ] Multi-tenant isolation: user A cannot see user B's document via any query path

**Run:**
- Upload sample.pdf to Supabase Storage under `uploads/{user_id}/`
- `curl -X POST -H "Authorization: Bearer <jwt>" -d '{"storage_path":"...","filename":"...","mime_type":"application/pdf","size_bytes":123}' http://localhost:8000/ingest`

---

### [FEAT-008] `/documents` list + detail + delete
**Phase:** 1
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/routes/documents.py`
**Tests:**
- `apps/api/tests/test_documents.py`
**Acceptance criteria:**
- [ ] GET /documents returns paginated list scoped to JWT user
- [ ] GET /documents/{id} returns 404 for another user's doc
- [ ] DELETE /documents/{id} cascades to chunks, conversations reference, storage files
- [ ] DELETE while status='parsing' returns 409

**Run:**
- `curl -H "Authorization: Bearer <jwt>" http://localhost:8000/documents`

---

## Phase 2 — Retrieval + Generation

### [FEAT-009] Retriever service (hybrid + RRF)
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/retriever.py`
**Tests:**
- `apps/api/tests/test_retriever.py`
**Acceptance criteria:**
- [ ] `Retriever.retrieve(question, document_ids, user_id, k) -> list[Chunk]`
- [ ] Runs vector search (cosine) and BM25 (Postgres FTS) in parallel
- [ ] Merges via Reciprocal Rank Fusion (k=60 default)
- [ ] Returns top-k with metadata: chunk_id, content, page, document_name, element_type
- [ ] user_id is included in every SQL WHERE clause explicitly

**Run:**
- `pytest apps/api/tests/test_retriever.py -v`

---

### [FEAT-010] Gemini generator wrapper
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/generator.py`
- `.agent/api-docs/gemini.md` (via `/api-check gemini`)
**Tests:**
- `apps/api/tests/test_generator.py`
**Acceptance criteria:**
- [ ] `Generator.generate(question, chunks) -> GenerateResult` returns answer text + parsed citation markers
- [ ] System prompt instructs Gemini 3.6 Flash to cite chunk IDs inline as `[N]`
- [ ] Multimodal — figure chunks pass their image content to Gemini
- [ ] Returns metadata: model, input_tokens, output_tokens, latency_ms

**Run:**
- Before implementation: `/api-check gemini`

---

### [FEAT-011] Citation verifier
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Files:**
- `apps/api/services/verifier.py`
**Tests:**
- `apps/api/tests/test_verifier.py` — supported / partial / unsupported fixtures
**Acceptance criteria:**
- [ ] `Verifier.verify(claim, chunk) -> Verdict{verdict, quote}` uses Gemini 3.5 Flash-Lite
- [ ] Verdict enum: supported | partial | unsupported
- [ ] Returns the supporting quote from the source (or null if unsupported)
- [ ] Batches verifications per generate call

**Run:**
- `pytest apps/api/tests/test_verifier.py -v`

---

### [FEAT-012] `/query` endpoint
**Phase:** 2
**Status:** planned
**Owner:** claude-code
**Depends on:** FEAT-009, FEAT-010, FEAT-011
**Files:**
- `apps/api/routes/query.py`
**Tests:**
- `apps/api/tests/test_query.py`
- `apps/api/tests/e2e/test_query_e2e.py`
**Acceptance criteria:**
- [ ] POST /query returns 200 with answer + citations per API_CONTRACT.md
- [ ] Unsupported citations are dropped from response, markers stripped from answer text
- [ ] Creates conversation + message + citations rows atomically
- [ ] Continuing an existing conversation appends messages correctly
- [ ] Cross-tenant document_ids in request → 403

**Run:**
- `curl -X POST -H "Authorization: Bearer <jwt>" -d '{"question":"...","document_ids":["..."]}' http://localhost:8000/query`

---

## Phase 3 — Frontend + Auth

### [FEAT-013] Next.js app shell + Supabase Auth
**Phase:** 3
**Status:** planned
**Owner:** claude-design + gemini (auth wiring)
**Files:**
- `apps/web/app/layout.tsx`
- `apps/web/app/(auth)/login/page.tsx`
- `apps/web/app/(auth)/signup/page.tsx`
- `apps/web/middleware.ts`
- `apps/web/lib/supabase/browser.ts` + `server.ts`
**Tests:**
- `apps/web/e2e/auth.e2e.ts`
**Acceptance criteria:**
- [ ] Login with email/password works
- [ ] Signup creates a Supabase Auth user
- [ ] Google OAuth flow works
- [ ] Protected routes redirect unauthenticated users to /login
- [ ] Layout has navigation shell (sidebar + top bar)

---

### [FEAT-014] Upload UI + document list
**Phase:** 3
**Status:** planned
**Owner:** claude-design
**Files:**
- `apps/web/app/(app)/documents/page.tsx`
- `apps/web/components/documents/upload-zone.tsx`
- `apps/web/components/documents/document-card.tsx`
- `apps/web/lib/api/client.ts`
**Tests:**
- `apps/web/e2e/upload.e2e.ts`
**Acceptance criteria:**
- [ ] Drag-drop or click-to-select PDF
- [ ] Upload progress bar
- [ ] Document appears in list immediately with status 'parsing'
- [ ] Status updates via polling (initial: every 2s while not ready/failed, max 60s)
- [ ] Delete confirmation modal
- [ ] Empty state when no docs
- [ ] Error state when upload fails

---

### [FEAT-015] Chat UI + citation source panel
**Phase:** 3
**Status:** planned
**Owner:** claude-design
**Files:**
- `apps/web/app/(app)/chat/[conversation_id]/page.tsx`
- `apps/web/components/chat/message-bubble.tsx`
- `apps/web/components/chat/citation-chip.tsx`
- `apps/web/components/chat/source-panel.tsx`
- `apps/web/components/chat/question-input.tsx`
**Tests:**
- `apps/web/e2e/chat.e2e.ts`
**Acceptance criteria:**
- [ ] Question input at bottom, messages scroll above
- [ ] Assistant messages render inline citation chips
- [ ] Click citation → source panel opens with chunk content + page image (if figure)
- [ ] Verdict-based citation styling (supported = solid, partial = dashed warning, no unsupported shown)
- [ ] Loading state during retrieval + generation
- [ ] Auto-scroll to newest message
- [ ] Mobile-responsive (source panel becomes bottom sheet)

---

## Phase 4 — Polish (see SCOPE.md for full list)

Features here are placeholders until Phase 3 ships. Do not start unless Phase 3 is `complete`.

- [FEAT-016] Streaming responses via SSE — planned
- [FEAT-017] OCR fallback via Gemini Flash — planned
  - **Trigger (updated 2026-07-22, FEAT-004 session):** *not* "low Docling confidence" — no such signal exists. `Parser.parse()` (FEAT-004) exposes no confidence score to threshold on; Docling just silently returns fewer/zero elements for pages it can't read, indistinguishable from a legitimately blank page. Confirmed against the `scanned.pdf` fixture: with OCR off, a fully-scanned 3-page PDF returned 2 elements total, 0 on 2 of the 3 pages, no error. The trigger must be a positive heuristic evaluated over `ParsedDocument`, e.g.:
    - Element count for a page falls below some threshold relative to what a page with real content typically produces, or
    - Zero elements on a page that isn't the last page of a short document (near-certain sign of missed content, not a genuinely blank page)
  - Original wording ("for low-confidence Docling pages," still in SCOPE.md and ARCHITECTURE.md as of this edit) assumed a confidence signal that doesn't exist — see CHANGELOG.md 2026-07-22 "feature: Docling parser service (FEAT-004)" for the full finding.
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

*(empty — populate as suggestions are promoted)*
