# 2026-07-23 Efficiency Review — FEAT-000 through FEAT-008

Scope: reconfirmation of `.agent/reviews/2026-07-23-perf.md` against the current tree (post
CVE-remediation dependency bumps, post-FEAT-008), plus two items that review explicitly deferred
without ever being concretely assessed, plus FEAT-008-specific efficiency questions.

Validation performed:
- Re-read `apps/api/routes/ingest.py`, `apps/api/services/parser.py`, `apps/api/services/embedder.py`, `apps/api/routes/documents.py`, `apps/api/db/queries.py`, `apps/api/db/client.py` against the current committed tree (`d20146f`), not against perf.md's line numbers (several have shifted since FEAT-007's Codex-review and security-fix follow-ups moved code around).
- Read Docling's installed `document_converter.py` and `datamodel/settings.py` source directly to assess pipeline-sharing thread-safety, rather than assuming.
- Read Starlette's `background.py` source directly to confirm how `BackgroundTasks` actually dispatches sync callables.
- Confirmed `supabase-py` ships a separate async client factory (`acreate_client`), not currently used anywhere in this codebase.
- Pulled real elapsed-time numbers from this session's own recent full-suite and e2e runs (not re-profiled from scratch — the numbers already exist and are recent).

## Part 1 — Reconfirming `2026-07-23-perf.md`

All four `[WARNING]`-level findings are **still accurate as written** — nothing has fixed them, and nothing since has made them meaningfully worse either, with one scope change noted below.

- **Parse dominates ingest time, no per-stage timing exists** — still true. No timing instrumentation was added anywhere in `run_ingest_pipeline`. The CVE-remediation dependency bump (`docling` 2.87.0 → 2.94.0) did **not** materially change this: the real e2e test was re-run twice after that bump (once right after the dependency PR, once during fresh-clone verification) and produced elapsed times of 88.14s and consistent chunk/page counts for `table_heavy.pdf`, in the same range as the pre-bump 72.70s figure — within normal run-to-run variance for a CPU-bound local model-inference workload, not a regression. `docling-parse` (the actual PDF-parsing backend, separate package) was deliberately held at 5.11.0 during that upgrade specifically to avoid a major-version jump — see `apps/api/pyproject.toml`'s comment on the `docling` pin — so the parsing-hot-path code itself is materially unchanged, which is consistent with the stable timing.
- **Parser/Embedder constructed per ingest** — still true, confirmed by re-reading the current file (`parser = parser or Parser()`, `chunker = chunker or Chunker()`, `embedder = embedder or Embedder()`, still inside `run_ingest_pipeline`'s `try` block). This is Part 2 below — the deferred item.
- **BackgroundTasks architecture has no queue/timeout** — still true, completely unchanged in code. `.agent/SCOPE.md` is consolidated as part of this review (see bottom of this doc) to state this once clearly instead of three scattered bullets.
- **Memory risk grows with figure-heavy documents** — still true, unchanged. FEAT-008 doesn't make this worse: `DELETE /documents/{id}` only ever handles `figure_path` strings and Storage object keys, never loads image bytes into process memory.

The four `[INFO]`-level "no issue" findings **still hold**, with one scope change:

- **No app-code N+1 in the ingest path** — still true for ingest. FEAT-008 extends the same good pattern for `GET /documents` and `GET /documents/{id}` (single query each, no per-row follow-ups) and for `DELETE`'s figure cleanup (one batched `.remove(figure_paths)` call, not one call per figure). One narrow exception found — see Part 3.
- **Embedder batching logic is single-batch in practice** — unaffected, still true.
- **Chunking is not a performance concern** — unaffected, still true.
- **Database/Storage clients constructed more often than necessary** — still true, and the footprint has **grown**, not just persisted: perf.md counted two `get_service_role_client()` calls per ingest request (`post_ingest`, `run_ingest_pipeline`). FEAT-008 adds three more call sites — `list_documents`, `get_document`, and `delete_document` each construct their own fresh client at the top of the handler. Same verdict as before (fine to defer — each construction is cheap, no network I/O happens until the first real call), just noting the finding now covers five call sites instead of two, worth knowing if this is ever addressed so the fix covers all of them at once rather than being rediscovered per-router.

### Genuinely new this pass (not in the prior review)

**Every route handler in this codebase is `async def` but makes fully synchronous, blocking I/O calls with no `await` anywhere in the function body.** `post_ingest`, `list_documents`, `get_document`, and `delete_document` all call the sync `supabase-py` client (`get_service_role_client()` returns a client built via `supabase.create_client`, not the library's separate `acreate_client`). FastAPI only offloads blocking work to a thread pool automatically for handlers declared as plain `def`; for `async def` handlers, whatever runs inside executes directly on the single event loop. Every Postgrest/Storage HTTP round-trip in these four handlers currently blocks that event loop for its full duration — not just for the requesting client, but for any other concurrent request this worker process is trying to serve at the same time. This was true in FEAT-007's `post_ingest` too (in scope for the prior review) but wasn't called out there; it's directly relevant now because it's the actual answer to task item 3's storage-concurrency question, below.

At single-user local-portfolio scale this is invisible — there's no concurrent load to contend with. It becomes real the moment there's more than trivial concurrent traffic on one worker process. Fix, if it's ever warranted, is well-defined and small: switch to `supabase.acreate_client()` (confirmed present in the installed `supabase-py`, not currently used anywhere) and `await` the calls, or wrap the sync calls in `anyio.to_thread.run_sync`/`asyncio.to_thread`. Not recommending either now — flagging it because it changes the shape of the answer to task item 3, not because it's worth fixing today.

## Part 2 — Parser/Embedder process-lifetime reuse: concrete thread-safety assessment

This has been deferred twice (original perf review, then again by name in this task) without ever being actually assessed. Here's the assessment, from source, not from assumption.

**First, the premise this depends on is real, not hypothetical:** Starlette's `BackgroundTask.__call__` (`starlette/background.py`) checks `is_async_callable(func)`; since `run_ingest_pipeline` is a plain `def`, it's dispatched via `run_in_threadpool(self.func, ...)` — `anyio.to_thread.run_sync` under the hood, which runs on a real OS worker thread from anyio's thread pool (default capacity well above what a few concurrent requests would need). Concretely: **two `POST /ingest` requests arriving close together, right now, today, with zero further architecture changes, can and do run two `run_ingest_pipeline` invocations truly concurrently on separate threads within the same worker process.** This isn't a Phase-5-only concern — it's live in the current codebase.

**Embedder — verdict: yes, safe to share as a process-lifetime singleton.**
- `Embedder._tokenizer` is a lazily-populated, write-once cache (`self._tokenizer = self._client.tokenizer(MODEL)` if `None`). Two threads racing to populate it for the first time is a real but *benign* race: `voyageai.Client.tokenizer()` is itself `@functools.lru_cache()`'d at the SDK level (confirmed during FEAT-006), so both threads get back the same cached `tokenizers.Tokenizer` object regardless of which one "wins" the assignment — no corruption, just a harmless redundant lookup on first use.
- The underlying `tokenizers` library (Rust-backed) is explicitly designed for concurrent `encode()` calls from multiple threads — this is one of its stated design goals, not an incidental property.
- `voyageai.Client.multimodal_embed()` makes HTTP calls through a client that is safe for concurrent use across threads (standard, well-established behavior for httpx-based clients making independent requests — no shared mutable request/response state between calls).
- No other mutable instance state exists on `Embedder`.

**Parser/DocumentConverter — verdict: architecturally supported, but not confidently safe without an accompanying serialization guard.** This is the one that needed real source-reading, not a hand-wave:
- `DocumentConverter.initialized_pipelines` (the lazy per-pipeline-config cache) is guarded by a **module-level** `threading.Lock` (`_PIPELINE_CACHE_LOCK`, `docling/document_converter.py`) — safe for concurrent first-time pipeline construction.
- But that lock only protects the cache *dict access*. The actual `pipeline.execute(in_doc, ...)` call (`_execute_pipeline`, same file) runs **outside** the lock, against a pipeline object now shared across however many threads are concurrently parsing.
- Docling's own architecture already does exactly this internally: `_convert()`'s batch path uses a `ThreadPoolExecutor` (`settings.perf.doc_batch_concurrency` workers) to call `_process_document` concurrently across a shared pipeline when converting a batch of documents in one `convert_all()` call. So sharing one `DocumentConverter` across concurrent parses is a pattern Docling's own maintainers built and ship.
- However: `docling/datamodel/settings.py` sets `doc_batch_concurrency: int = 1` **by default**, with the comment *"Warning: Experimental! No benefit expected without free-threaded python."* That's Docling's own maintainers explicitly hedging on this exact scenario under standard (GIL-enabled) CPython — which is what this project runs on. It's not labeled broken, but it's not vouched for either.

**Concrete recommendation** (assessment only — not implemented in this pass, per this task's scope):
- Share **one `Embedder` instance** at process/module lifetime. Low risk, real win (avoids re-resolving the tokenizer and reconstructing the Voyage HTTP client on every ingest).
- Share **one `DocumentConverter`/`Parser` instance** at process/module lifetime too — the expensive part (model weight loading, pipeline construction) is exactly what the shared cache avoids repeating, and that part is lock-protected already. But wrap the actual `.parse()` call in a **process-level `threading.Lock`** (or a `Semaphore(1)`) so concurrent background tasks serialize on `execute()` itself rather than relying on unverified concurrent-inference safety. This captures effectively all of the reuse benefit (construction/loading is the dominant cost — 86.55s parse vs. sub-millisecond chunking) while sidestepping the one part Docling itself won't fully vouch for. Under the GIL, concurrent parses wouldn't have gotten much true wall-clock parallelism anyway for the Python-level orchestration; the lock mainly forecloses the specific unverified risk (concurrent execution inside the C-extension model-inference code) rather than costing real throughput.
- **This reuse pattern is fully independent of the Phase 5 worker-pool/queue redesign.** It's a same-process, same-request-model change — a module-level singleton (or a small lifespan-managed instance on `app.state`), not a queue, not a separate worker process. Nothing about it needs to wait for Phase 5.

## Part 3 — FEAT-008 specifics

**Storage removal concurrency (`delete_document`'s two `.remove()` calls):** the honest answer is that `asyncio.gather` isn't the right tool here at all, independent of whether it'd be a "cheap win" — `client.storage.from_(...).remove(...)` are synchronous blocking calls (see Part 1's new finding), not coroutines; `asyncio.gather` expects awaitables. Getting real concurrency would require either switching to `acreate_client()`'s async storage client and awaiting both `.remove()` calls together, or wrapping the two sync calls in `asyncio.to_thread()`. At current scale (two Storage HTTP calls, each tens of milliseconds against local infra) neither is worth the added complexity — sequential is fine. Worth revisiting only if `acreate_client()` ever gets adopted project-wide for the blocking-event-loop reason in Part 1, at which point concurrent storage cleanup would come essentially for free as part of that larger change, not as its own project.

**Pagination N+1 shape:** `GET /documents` (`queries.list_documents`) is a single query with `.limit(limit + 1)` — no per-row follow-up queries, confirmed by reading the current `db/queries.py`. `GET /documents/{id}` is a single query. `DELETE`'s figure cleanup reads all `figure_path` values in one query and removes them in one batched `.remove(figure_paths)` call, not a per-figure loop — this holds up as figure counts grow.

One narrow exception, found while checking this: `queries.remove_document_from_conversations` loops over every matching conversation and issues one `.update()` call per conversation (`for conversation in conversations: ... .update(...).eq("id", conversation["id"]).execute()`). This *is* N+1-shaped in the number of conversations referencing a given document. **Not worth acting on**: Phase 2 (`/query`, conversations) doesn't exist yet, so this code path has zero real callers today — it's exercised only by FEAT-008's own synthetic test that inserts one conversation row directly. Even once Phase 2 exists, a single document being referenced by more than a handful of one user's own conversations is not a realistic scale concern. Flagging for the record, not for action.

## Part 4 — Test suite runtime (informational)

Real numbers from this session's own recent runs, not re-measured for this review:

| Slice | Time |
|---|---|
| Full suite (122 passed, 2 skipped) | ~570–680s (9.5–11.3 min), run-to-run |
| `test_parser.py` alone (15 tests) | ~447–460s (7.5–7.7 min) — the dominant cost |
| Everything else combined (health, auth, migrations, chunker, embedder, ingest, documents) | roughly 2–3 min total |

The parser suite is the overwhelming majority of total runtime, and it's real Docling parsing of three fixture PDFs across 15 tests — including several tests that specifically exist to exercise parse-*failure* paths (`test_invalid_pdf_bytes_raises_parse_error`, `test_truncated_pdf_raises_parse_error`, etc.), each of which needs its own genuine Docling invocation and can't be collapsed into the module-scoped fixture the successful-parse tests already share. This is the direct, expected cost of this project's own stated testing discipline (real infrastructure over mocks) applied to a CPU-bound parsing library — not an accident or a regression to chase.

**Verdict: not a problem worth fixing.** Per this task's own framing, informational only. If it ever becomes real iteration friction, `pytest -k "not test_parser"` (or scoping to whichever file is actually relevant to the change at hand — this session did exactly that throughout, running individual test files rather than the full suite while iterating) is a zero-cost, already-available escape hatch. No test-splitting, parallelization, or CI infrastructure investment is warranted at this project's current scale or team size of one.

## Summary — what's proportional to act on vs. not

**True but not worth acting on at this scale** (explicitly, per this task's request):
- All four perf.md WARNINGs remain — each already carries its own "must-fix-before-X" framing tied to a future milestone (public demo, real concurrent traffic), not to today.
- Every `async def` route being secretly fully synchronous (Part 1's new finding).
- `delete_document`'s sequential Storage removal (Part 3).
- The `remove_document_from_conversations` N+1 shape (Part 3) — doubly so, since it's currently dead code.
- Client-construction-per-request footprint growing with FEAT-008 (Part 1).
- Test suite runtime (Part 4).

**Genuinely cheap, clearly-worth-it regardless of scale** (worth doing whenever this area gets touched next, not urgent):
- Sharing one `Embedder` instance at process lifetime — low risk (assessed concretely above, not a hand-wave), real latency/memory win, zero architectural dependency on Phase 5.
- Sharing one `DocumentConverter`/`Parser` instance at process lifetime, paired with a lock around the actual `.parse()` call — same reasoning, slightly more nuanced given Docling's own experimental-concurrency caveat, still independent of Phase 5.

Neither of the two "worth it" items is implemented in this pass — this is a findings review, not a change.
