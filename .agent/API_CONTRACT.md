# API Contract

Specification for the internal FastAPI endpoints. Both `apps/api` (Pydantic models) and `apps/web` (TS types) must conform to this document. Changes require a CHANGELOG entry and a version bump if breaking.

**Scope:** internal endpoints only. External API references (Voyage, Gemini, Supabase, Docling) live under `.agent/api-docs/` and are populated by `/api-check`.

---

## Base URL

- Development: `http://localhost:8000`
- Production: `https://docify-api.onrender.com` — real, deployed 2026-07-27 (Render web service `docify-api`, Docker runtime, connected to the real production Supabase project). `/health` and authenticated `GET /conversations` verified live with a real Supabase-issued JWT. **Known limitation, not yet resolved:** `POST /ingest` reliably OOM-crashes the free-tier instance (512MB RAM) during real Docling parsing — confirmed live, not assumed (Render platform logs show the process going silent mid-model-load, no Python traceback, followed by an automatic restart ~2 minutes later — the signature of a kernel OOM-kill, not a caught exception). The CPU-only-torch fix (pyproject.toml, 2026-07-27) was necessary and fixed a *different*, earlier OOM at container *startup*, but real inference memory still exceeds what the free tier provides. See CHANGELOG.md's deploy entry for the full investigation.

## Auth model

All non-`/health` endpoints require a Supabase JWT as `Authorization: Bearer <token>`.

The FastAPI middleware:
1. Extracts the JWT
2. Verifies signature via Supabase's JWKS endpoint (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), algorithm `ES256` — this project uses Supabase's asymmetric signing keys, not the legacy shared HS256 secret. Keys are resolved by `kid` via `jwt.PyJWKClient`.
3. Extracts `sub` claim as `user_id`
4. Attaches `user_id` to the request state
5. Rejects with `401` on any failure

`user_id` is **never accepted from the request body** for user-owned resources — always derived from the JWT.

**Note on `SUPABASE_JWT_SECRET`:** still present in `.env` and `.env.example`, but currently unused by the auth middleware — it's the legacy HS256 shared secret, and this project's Supabase instance issues ES256 tokens verified via JWKS instead. Kept in case a future Supabase config change reverts to legacy signing, or another service needs it. FEAT-003 originally implemented HS256-against-this-secret verification and it was wrong for this project; see CHANGELOG 2026-07-22 and MEMORY.md §Anti-patterns for how that was caught.

## Standard error envelope

All errors return this shape with the appropriate HTTP status:

```json
{
  "error": {
    "code": "STRING_ENUM",
    "message": "Human-readable summary",
    "detail": { "optional": "additional structured info" }
  }
}
```

### Common error codes

| Code | HTTP | Meaning |
|---|---|---|
| `UNAUTHORIZED` | 401 | Missing or invalid JWT |
| `FORBIDDEN` | 403 | Authenticated but not allowed (e.g. accessing another user's doc) |
| `NOT_FOUND` | 404 | Resource doesn't exist or isn't visible to user |
| `CONFLICT` | 409 | Action not allowed given the resource's current state (e.g. deleting a document that's still being parsed) — added 2026-07-23, FEAT-008, was missing despite `DELETE /documents/{id}`'s own spec already documenting a 409 |
| `VALIDATION_ERROR` | 422 | Request body failed schema validation |
| `RATE_LIMITED` | 429 | Either a downstream API's own free-tier ceiling was hit, or (added 2026-07-28, FEAT-024) this app's own proactive per-user rate limit on `POST /ingest`/`POST /query`/`POST /query/stream` was hit — see those endpoints' entries below for the real, vendor-quota-derived limit values and reasoning. A `Retry-After` header (seconds) is included on the proactive-limit case. |
| `PARSE_FAILED` | 500 | Docling could not parse the document |
| `EMBED_FAILED` | 502 | Voyage API call failed |
| `GENERATE_FAILED` | 502 | Gemini API call failed |
| `STORAGE_ERROR` | 500 | A Supabase Storage call failed (e.g. `DELETE /documents/{id}` couldn't remove a file) — transient infrastructure failure, not a client error; the resource being acted on is left unmodified and retrying is safe. Added 2026-07-23, FEAT-008 follow-up |
| `INTERNAL` | 500 | Anything unexpected |

---

## Endpoints

### `GET /health`
No auth. Returns service liveness for uptime monitors.

**Response 200:**
```json
{ "status": "ok", "version": "0.1.0", "timestamp": "2026-07-22T14:30:00Z" }
```

---

### `POST /ingest`
Kicks off document parsing + embedding for a file already uploaded to Supabase Storage.

**Request:**
```json
{
  "storage_path": "uploads/{user_id}/{uuid}.pdf",
  "filename": "annual-report-2025.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 2413056
}
```

**Response 202 (accepted, processing async):**
```json
{
  "document_id": "3f9e...",
  "status": "uploaded",
  "created_at": "2026-07-22T14:30:00Z"
}
```

**Behaviour:**
- Creates `documents` row with `status='uploaded'`, returns `202` immediately — the response's `status` field reflects that literal, just-inserted DB value (**corrected 2026-07-23, FEAT-007**: an earlier draft of this example showed `"status": "parsing"`, which the endpoint never actually returns synchronously — `parsing` is set moments later, inside the background task, after the response has already gone out)
- Downloads file, parses with Docling, chunks, embeds, uploads figures to Storage, inserts chunks — this happens in a background task (FastAPI BackgroundTasks for v1; queue system if scale demands). Status progresses `uploaded` → `parsing` → `embedded` → `ready` (or `failed` at any stage, with `documents.error` populated). `parsed_at`/`embedded_at` are stamped as milestones inside the `parsing` phase — there's no dedicated status value for "parsed, not yet embedded"
- Client polls `GET /documents/{id}` or subscribes via Supabase Realtime to observe status transitions

**Errors:**
- `403 FORBIDDEN` if `storage_path` does not start with `uploads/{jwt.user_id}/`
- `422 VALIDATION_ERROR` if mime_type unsupported
- `429 RATE_LIMITED` (added 2026-07-28, FEAT-024) — **2 requests/minute** and **10 requests/day**, per user (`request.state.user_id`, never IP). Real vendor-quota-derived, not round numbers: each ingest makes ~1 Voyage embed call against Voyage's real, shared 3 RPM free-tier ceiling (`.agent/MEMORY.md`); a scanned document's OCR fallback can make several `gemini-2.5-flash` calls against that model's real, shared 20/day ceiling (also `.agent/MEMORY.md`) — both budgets are shared across every user of this app, not per-user, so per-user limits are deliberately tight. Full reasoning in `apps/api/routes/ingest.py`'s own comment.

---

### `GET /documents/{document_id}`
Returns document metadata + current status.

**Response 200:**
```json
{
  "id": "3f9e...",
  "filename": "annual-report-2025.pdf",
  "page_count": 42,
  "status": "ready",
  "error": null,
  "created_at": "2026-07-22T14:30:00Z",
  "parsed_at": "2026-07-22T14:30:35Z",
  "embedded_at": "2026-07-22T14:31:12Z"
}
```

**Errors:**
- `404 NOT_FOUND` if document doesn't exist or `user_id` mismatch

---

### `GET /documents`
Lists the user's documents.

**Query params:**
- `status` (optional) — filter by status
- `limit` (default 50, max 200)
- `cursor` (opaque, for pagination)

**Response 200:**
```json
{
  "documents": [ { /* same shape as GET /documents/{id} */ } ],
  "next_cursor": "opaque-string-or-null"
}
```

---

### `DELETE /documents/{document_id}`
Deletes the document, its chunks, its figures in storage, and all references from conversations. Cascade is handled by the schema.

**Response 204:** empty body

**Errors:**
- `404 NOT_FOUND`
- `409 CONFLICT` if document is currently being processed — `status in ('parsing', 'embedded')` (**corrected 2026-07-25**: originally only checked `'parsing'`; `'embedded'` still has a real in-flight background task too — figure upload, chunk insert, and `mark_ready()` all happen strictly after `mark_embedded()` — found via FEAT-014's live UI testing, see `.agent/GAPS.md`)

---

### `POST /query`
Ask a question over one or more documents.

**Request:**
```json
{
  "question": "What was Q3 revenue?",
  "document_ids": ["3f9e...", "8a2c..."],
  "conversation_id": "optional-existing-conv-id",
  "k": 8
}
```

**Response 200:**
```json
{
  "conversation_id": "6c1a...",
  "message_id": "9f4d...",
  "answer": "Q3 revenue was $4.2M [1], up 18% year-over-year [2], driven by strong international demand [3].",
  "citations": [
    {
      "marker": 1,
      "chunk_id": "b2e0...",
      "document_id": "3f9e...",
      "document_name": "annual-report-2025.pdf",
      "document_mime_type": "application/pdf",
      "page_number": 14,
      "element_type": "text",
      "snippet": "Third-quarter revenue totaled $4.2 million...",
      "verdict": "supported",
      "supporting_quote": "Third-quarter revenue totaled $4.2 million"
    },
    {
      "marker": 2,
      "chunk_id": "c9f1...",
      "document_id": "3f9e...",
      "document_name": "annual-report-2025.pdf",
      "document_mime_type": "application/pdf",
      "page_number": 14,
      "element_type": "table",
      "snippet": "| Q2 | $3.56M | | Q3 | $4.20M |",
      "verdict": "supported",
      "supporting_quote": "Q3 $4.20M"
    },
    {
      "marker": 3,
      "chunk_id": "d4a7...",
      "document_id": "3f9e...",
      "document_name": "annual-report-2025.pdf",
      "document_mime_type": "application/pdf",
      "page_number": 15,
      "element_type": "text",
      "snippet": "Growth was broad-based across all regions this quarter.",
      "verdict": "partial",
      "supporting_quote": "Growth was broad-based across all regions this quarter"
    }
  ],
  "metadata": {
    "model": "gemini-3.6-flash",
    "verifier_model": "gemini-3.5-flash-lite",
    "retrieved_count": 8,
    "cited_count": 3,
    "latency_ms": 3420
  }
}
```

**Behaviour:**
- If `conversation_id` omitted, creates a new conversation
- If `conversation_id` provided, appends to it (must belong to user)
- Runs hybrid retrieval → generation → verification pipeline (see ARCHITECTURE.md)
- `verdict` is one of `supported` | `partial` | `unsupported` (see ARCHITECTURE.md's verify flow), and each is treated differently in this response:
  - `supported` — citation is kept in the `citations` array as-is; the answer text keeps its `[N]` marker
  - `partial` — citation is **kept** in the `citations` array (same as `supported`, never dropped); the client renders it with a warning indicator, since the source only partially backs the claim (e.g. the marker-3 example above: the source confirms broad-based growth but not specifically "international demand" — kept so the reader can judge the source themselves, not silently hidden)
  - `unsupported` — citation is **dropped** from the `citations` array; the corresponding `[N]` marker is stripped from `answer`
- **`figure_url`** (added FEAT-026): present only on a citation with `element_type: "figure"` — a signed, time-limited Storage URL (600s) for that figure's image. **Omitted entirely** (not sent as `null`) on `text`/`table` citations, and on a `figure` citation whose image fetch itself failed server-side (that citation's `element_type` degrades to `"text"` in that case — see `services/figure_fetcher.py` — so `figure_url` never appears alongside a lie about what the citation actually is). Not persisted anywhere — built fresh from the citation's `chunk_id` → `figure_path` on every read, live or historical (`GET /conversations/{id}/messages` below produces byte-identical citation shapes from stored data, including a freshly-signed `figure_url` each time it's read, not a cached/expired one).
- **`page_number`** (FEAT-020, extended 2026-07-27 to cover DOCX/PPTX/HTML): the example above is from a PDF source, where this is a real PDF page number. For a **PPTX**-sourced citation it's the real slide index instead (same field, same 1-indexed meaning of "position within the source"). For a **DOCX/HTML**-sourced citation it is always `1` — Docling provides no page/location concept for these two formats at all (confirmed empirically, `.agent/SCHEMA.md`'s `chunks.page_number` note has the full investigation), so every citation from the same DOCX/HTML document reports the same value.
- **`document_mime_type`** (added 2026-07-27, closing the gap the note above used to describe): the citation's source document's real mime type (`documents.mime_type`) — this is what the client actually uses to know whether `page_number` means a real page, a slide index, or a meaningless sentinel. Required a migration (`match_chunks_by_vector`/`match_chunks_by_fts` gained this as a new output column — Postgres does not allow `CREATE OR REPLACE FUNCTION` to change `RETURNS TABLE` columns, confirmed live, so this needed a real `DROP FUNCTION` + `CREATE FUNCTION`). The frontend's `citationLocation()` (`lib/chat/parse-message.ts`) is the one place this becomes display text: "Page N" for PDF/unrecognized mime types, "Slide N" for PPTX, omitted entirely for DOCX/HTML rather than showing a false "Page 1". Verified in a real browser against all 4 formats.

**Errors:**
- `403 FORBIDDEN` if any `document_ids` don't belong to user
- `422 VALIDATION_ERROR` if `document_ids` empty or `question` empty
- `502 GENERATE_FAILED` if Gemini call fails after retries
- `429 RATE_LIMITED` (added 2026-07-28, FEAT-024) — **3 requests/minute** and **40 requests/day**, per user. Each real call makes one Voyage `embed_query` call (same shared 3 RPM ceiling `/ingest` draws on) plus one `gemini-3.6-flash` generation call and one `gemini-3.5-flash-lite` verification call per cited claim — a separate quota bucket from `/ingest`'s OCR ceiling, whose exact daily limit is unconfirmed (`.agent/MEMORY.md`), so 40/day is a deliberately generous-but-real bound rather than a derived vendor number. **Shares one combined counter with `POST /query/stream` below** — they're the same underlying action with two response-delivery mechanisms, not two independent budgets. Full reasoning in `apps/api/routes/query.py`'s own comment.

---

### `POST /query/stream` (FEAT-016, 2026-07-27)
SSE streaming variant of `POST /query` — same request body, same auth/ownership/history validation (run to completion **before** the stream opens, so an invalid `document_id`/JWT/conversation always comes back as a normal JSON error response with the codes above, never as a stream that starts and then errors out). A separate route rather than a mode flag on `/query`: `response_model=QueryResponse` validation and a `StreamingResponse` are mutually exclusive in FastAPI, and `/query`'s synchronous contract stays untouched for any caller that doesn't want SSE.

Browsers' `EventSource` can't send a POST body or an `Authorization` header — clients must use `fetch()` with a manually-read stream (see `apps/web/lib/api/query.ts`'s `askQuestionStream()`), not `EventSource`.

**Rate limiting (added 2026-07-28, FEAT-024):** shares `POST /query`'s combined 3/minute + 40/day per-user counter (see that endpoint's entry) — a rate-limited request returns a normal `429 RATE_LIMITED` JSON response (the standard error envelope, `Content-Type: application/json`) **before the stream ever opens**, never an SSE connection that starts and then errors. Confirmed live, not assumed from decorator-ordering alone: `apps/api/tests/test_rate_limit.py`.

**Response:** `Content-Type: text/event-stream`, one `event: <type>\ndata: <json>\n\n` frame per event. Real, fixed event sequence — no event is ever skipped or reordered:

```
retrieving -> token* (zero or more) -> verifying -> citations-resolved -> done
```

`error` can replace any step from `token` onward and always terminates the stream — there is no path that closes the connection without either a `done` or an `error`.

| Event | `data` shape | Meaning |
|---|---|---|
| `retrieving` | `{}` | Retrieval has started. |
| `token` | `{"text": "..."}` | One raw text delta from Gemini, in arrival order. May contain unresolved `[N]` citation brackets — **must render as plain inert text, never styled or clickable**, since verification hasn't run yet. |
| `verifying` | `{}` | Generation is complete; citation verification has started. Claim-span extraction needs the full answer text, so this can never start earlier — there is no way to verify progressively. |
| `citations-resolved` | `{"conversation_id", "message_id", "answer", "citations"}` | Same `citations` shape as `POST /query`'s response (including the `supported`/`partial`-kept, `unsupported`-dropped-and-marker-stripped rule). `answer` is the **final**, marker-stripped text — clients should replace whatever raw text they'd accumulated from `token` events with this value, then re-parse citation markers against `citations` (see `buildAssistantMessage()`, reused for both the streaming and historical-message paths). |
| `done` | `{"metadata"}` | Same `metadata` shape as `POST /query`'s response. Terminal — the connection closes after this. |
| `error` | `{"code", "message"}` | Same error codes as the table above (`GENERATE_FAILED`, `VERIFY_FAILED`, `RETRIEVE_FAILED`, `PERSIST_FAILED`) — surfaced mid-stream instead of as an HTTP status, since the stream may already be open. Terminal. |

**UI contract for the gap between `token` and `citations-resolved`:** the client must show a distinct "verifying" indicator during this window — never let the fully-streamed-but-unverified text just sit there with no sign anything is still happening (`apps/web/components/chat/loading-stages.tsx`).

**Implementation note (real bug found and fixed during this feature, 2026-07-27):** every downstream call in the SSE generator (`Retriever.retrieve`, `Verifier.verify_batch`, `fetch_generator_chunks`, `signed_figure_url`, `create_query_turn`) is synchronous/blocking Python, run via `asyncio.to_thread(...)`. Calling `verify_batch()` directly (no `await`) was confirmed live to freeze the event loop for its entire real ~8s Gemini-verification duration, which meant uvicorn never got a chance to flush the already-yielded `verifying` frame to the socket until the *next* yield — the client received `verifying` and `citations-resolved` at the identical timestamp instead of with the real gap between them. Caught via real browser testing (DOM sampling + a temporary client-side event-arrival log), not from protocol-level tests alone, which happened to mask it. Only Gemini's own token-generation stream (`generate_content_stream`, genuinely async via `client.aio`) does not need this.

---

### `GET /conversations`
Lists user's conversations.

**Query params:** `limit`, `cursor`

**Response 200:**
```json
{
  "conversations": [
    {
      "id": "6c1a...",
      "title": "Q3 revenue analysis",
      "document_ids": ["3f9e..."],
      "message_count": 4,
      "updated_at": "2026-07-22T14:32:00Z"
    }
  ],
  "next_cursor": null
}
```

---

### `GET /conversations/{conversation_id}/messages`
Full message history for a conversation, including citations.

**Response 200:**
```json
{
  "conversation": { /* conversation object */ },
  "messages": [
    {
      "id": "9f4d...",
      "role": "user",
      "content": "What was Q3 revenue?",
      "created_at": "..."
    },
    {
      "id": "9f4e...",
      "role": "assistant",
      "content": "Q3 revenue was $4.2M [1]...",
      "citations": [ /* same shape as in POST /query */ ],
      "created_at": "..."
    }
  ]
}
```

---

### `DELETE /conversations/{conversation_id}`
Deletes the conversation and its messages + citations.

**Response 204**

---

## Not-yet-defined endpoints (Phase 4+)

- `POST /conversations/{id}/rename`
- `GET /conversations/{id}/export` — markdown export
- `PATCH /documents/{id}` — rename
- `POST /reindex/{document_id}` — re-run embedding after model upgrade

---

## Contract version

- Current: `v0.1` (unstable, breaking changes allowed before Phase 3 ships)
- After Phase 3 (frontend deployed): bump to `v1.0`, breaking changes require version bump
