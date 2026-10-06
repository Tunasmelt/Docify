# API Contract

Specification for the internal FastAPI endpoints. Both `apps/api` (Pydantic models) and `apps/web` (TS types) must conform to this document. Changes require a CHANGELOG entry and a version bump if breaking.

**Scope:** internal endpoints only. External API references (Voyage, Gemini, Supabase, OCR.space, parser libraries) live under `.agent/api-docs/` and are populated by `/api-check`.

---

## Base URL

- Development: `http://localhost:8000`
- Production: `https://docify-api.onrender.com` (Render, Docker runtime). Free-tier instances spin down after ~15 minutes idle, so the first request after a pause can take ~1 minute. `GET /health` reports the running commit.

## Auth model

All non-`/health` endpoints require a Supabase JWT as `Authorization: Bearer <token>`.

The FastAPI middleware:
1. Extracts the JWT
2. Verifies signature via Supabase's JWKS endpoint (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), algorithm `ES256` — this project uses Supabase's asymmetric signing keys, not the legacy shared HS256 secret. Keys are resolved by `kid` via `jwt.PyJWKClient`.
3. Extracts `sub` claim as `user_id`
4. Attaches `user_id` to the request state
5. Rejects with `401` on any failure

`user_id` is **never accepted from the request body** for user-owned resources — always derived from the JWT.

**`SUPABASE_JWT_SECRET`** (legacy HS256 shared secret) is listed in `.env.example` but unused — tokens are ES256, verified via JWKS. Kept only in case the Supabase project ever reverts to legacy signing.

## Standard error envelope

Every error the API itself returns uses this shape:

```json
{
  "error": {
    "code": "STRING_ENUM",
    "message": "Human-readable summary",
    "detail": { "optional": "additional structured info" }
  }
}
```

Two exceptions, both from the framework rather than route code: a request body/query param that fails Pydantic validation returns FastAPI's default `422 { "detail": [...] }`, and an unhandled exception returns a plain `500`.

### Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `UNAUTHORIZED` | 401 | Missing or invalid JWT (same message for every failure stage) |
| `FORBIDDEN` | 403 | Authenticated but not allowed — a `storage_path` outside the caller's prefix, or `document_ids` the caller doesn't own |
| `NOT_FOUND` | 404 | Resource doesn't exist **or** belongs to another user (deliberately indistinguishable) |
| `CONFLICT` | 409 | Not allowed in the resource's current state (e.g. deleting or re-indexing a document that is still processing) |
| `VALIDATION_ERROR` | 422 | A route-level check failed (empty question, unsupported mime type, bad cursor, bad title) |
| `RATE_LIMITED` | 429 | This API's per-user or global limit was hit (see `/ingest` and `/query`). Includes `Retry-After` (seconds) |
| `TOO_MANY_PROCESSING` | 429 | `/ingest` or `/reindex` while the caller already has 2 documents processing. The message says so; retry once one finishes |
| `GENERATE_FAILED` | 502 | The Gemini generation call failed |
| `STORAGE_ERROR` | 500 | A Supabase Storage call failed; the resource was left unmodified and retrying is safe |
| `DELETE_FAILED` | 500 | `DELETE /account` cleaned Storage but the final auth-user deletion failed; DB rows are untouched and retrying is safe |
| `RETRIEVE_FAILED` / `VERIFY_FAILED` / `PERSIST_FAILED` | — | Only sent as SSE `error` events on `/query/stream` (the HTTP status is already 200 by then) |

Ingest failures (parse, OCR, embedding) happen in the ingest worker after `202` has been returned, so they never surface as HTTP errors: the document moves to `status: "failed"` with a human-readable `error` string.

---

## Endpoints

### `GET /health`
No auth, not rate-limited. Liveness for uptime monitors, plus the commit the container is running.

**Response 200:**
```json
{ "status": "ok", "version": "0.1.0", "timestamp": "2026-07-22T14:30:00Z", "commit": "91436d1..." }
```

`commit` is Render's `RENDER_GIT_COMMIT`, or `"unknown"` outside Render. Compare it against the expected SHA before assuming a fix is deployed.

---

### `POST /ingest`
Starts parsing + embedding for a file the client has already uploaded to Supabase Storage.

**Request:**
```json
{
  "storage_path": "uploads/{user_id}/{uuid}.pdf",
  "filename": "annual-report-2025.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 2413056
}
```

Supported `mime_type`s: PDF, DOCX, PPTX, HTML.

**Response 202 (accepted, processing async):**
```json
{
  "document_id": "3f9e...",
  "status": "uploaded",
  "created_at": "2026-07-22T14:30:00Z"
}
```

**Behaviour:**
- Creates the `documents` row with `status='uploaded'` (queued), enqueues an ingest job, and returns `202` immediately.
- The ingest worker processes jobs one at a time across all users: it downloads, parses (with the OCR fallback for low-yield PDF pages), chunks, embeds, uploads figures, and swaps the new chunks in. Status moves `uploaded` → `parsing` → `embedded` → `ready`, or `failed` with `documents.error` set. `parsed_at`/`embedded_at` are milestone timestamps.
- **Retries:** a transient failure (rate limit, network, storage error) puts the document back to `uploaded` and retries after 60s, then 300s, up to 3 attempts in all; the last failure marks it `failed`. A job interrupted by a restart resumes within about 2 minutes.
- **Limits** (the document fails with a message saying which): over 50 MB (real size, after download), over 300 pages or slides, more than 30 pages needing OCR, or processing past 20 minutes. A missing upload fails at once.
- **Embedding fallback:** a batch whose Voyage retries are exhausted is embedded with Gemini `gemini-embedding-2` instead; `chunks.embedding_provider` records which. This is invisible to the client. A document only fails at this stage if both providers fail for the same batch.
- Clients poll `GET /documents` (or `GET /documents/{id}`) to observe status.

**Errors:**
- `403 FORBIDDEN` — `storage_path` is not under `uploads/{jwt.user_id}/`
- `422 VALIDATION_ERROR` — unsupported `mime_type`, or `size_bytes` over 50 MB
- `429 RATE_LIMITED` — any of:
  - **2/minute per user** (in memory)
  - **3/minute globally**, across all users (in memory) — keeps the whole app under Voyage's shared 3 RPM free-tier ceiling
  - **10/day per user** (Postgres `usage_counters`, so it survives Render restarts)

  `/ingest` and `/reindex` share all three counters. Limits derive from shared vendor quotas (Voyage 3 RPM; Gemini 2.5 Flash OCR 20/day) — reasoning in `routes/ingest.py`.
- `429 TOO_MANY_PROCESSING` — the caller already has **2 documents processing** (`uploaded`/`parsing`/`embedded`, started within the last 30 minutes). Checked before the daily limit, so a refused request doesn't use a daily slot. Ingest runs in the API process with no queue; this keeps one user from filling the 512 MB instance.

---

### `POST /reindex/{document_id}`
Re-runs the full ingest pipeline for an existing document: re-downloads the stored file, re-parses, re-chunks, re-embeds, and replaces its chunks.

**Request:** no body.

**Response 202:**
```json
{
  "document_id": "3f9e...",
  "status": "uploaded",
  "created_at": "2026-07-22T14:30:00Z"
}
```
Reports `uploaded` (queued): the route resets the document to `uploaded` and enqueues an ingest job.

**Use cases:**
1. Recovering a document the stuck-document reaper marked `failed` (see `GET /documents`).
2. Retrying Voyage for a document with Gemini-fallback chunks. This is a fresh attempt, not a forced provider — it can fall back again if Voyage is still limited.
3. Re-running the current parser (including OCR) on an older or previously failed document.

**Behaviour:**
- Clears `documents.error` and queues the same job as `/ingest` (`uploaded` → `parsing` → `embedded` → `ready` | `failed`), with the same retries and limits.
- **The existing chunks stay live** while it runs: new chunks are staged and swapped in atomically when it finishes, and old figure objects are removed after the swap. If reprocessing fails, the old chunks are kept. Citations that pointed at the old chunks are removed with them (`citations.chunk_id` cascades).

**Errors:**
- `404 NOT_FOUND`
- `409 CONFLICT` — the document is still processing (`status in ('parsing', 'embedded')`, or it already has a queued or running job)
- `429 RATE_LIMITED` — shares all of `/ingest`'s counters (one combined 10/day budget)
- `429 TOO_MANY_PROCESSING` — same 2-document cap as `/ingest`

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

**Behaviour — stuck-document reaper:** before building the list, any of this user's documents that have been in `parsing` or `embedded` for more than 30 minutes (`STUCK_DOCUMENT_THRESHOLD_SECONDS`) are set to `failed` with `error: "processing timed out, possibly interrupted by a service restart"`. This catches background tasks killed by a crash, OOM, or redeploy. The reaped document appears as `failed` in the same response. Recover it with `POST /reindex/{document_id}`.

---

### `GET /documents/{document_id}/pages/{page_number}/image`
Renders one page of a PDF as a PNG, for the citation page preview. With all four of `x0`, `y0`, `x1`, `y1` (a citation's `bbox`) the area is highlighted.

**Query params:** `x0`, `y0`, `x1`, `y1` (optional, PDF points from the page's top-left).

**Response 200:** `image/png` (110 dpi), `Cache-Control: private, max-age=86400`. Not rate-limited (no vendor API calls). The rendered page (without highlight) is cached in API memory per document and page (32 MB LRU), so repeat views skip the Storage download and render; ownership is checked before the cache.

**Errors:**
- `404 NOT_FOUND` — the document doesn't exist, isn't the caller's, or has no such page
- `422 VALIDATION_ERROR` — not a PDF (DOCX/HTML have no pages; PPTX slides aren't rendered), page < 1, or the page couldn't be rendered
- `500 STORAGE_ERROR` — the stored file couldn't be read

---

### `PATCH /documents/{document_id}`
Renames a document. Only the display name changes: parsing picks the format from `storage_path`, which stays as it was, and citations read the name live, so conversation history shows the new name too. Allowed in any status.

**Request:**
```json
{ "filename": "Q3 board report.pdf" }
```

**Response 200:** the updated document (same shape as `GET /documents/{document_id}`).

**Errors:**
- `422 VALIDATION_ERROR` — empty after trimming, longer than 255 characters, or contains control characters
- `404 NOT_FOUND` — doesn't exist or isn't the caller's

---

### `GET /documents/{document_id}/chunks/{chunk_id}/context`
"Show in document" for DOCX, PPTX and HTML citations, which have no page image. Returns the cited chunk with its surroundings, built from stored chunk text (the original file isn't rendered).

**Response 200:**
```json
{
  "kind": "slide",
  "label": "Slide 2",
  "blocks": [
    { "chunk_id": "…", "element_type": "text", "content": "Revenue reached $1,410,000", "cited": true, "figure_url": null }
  ]
}
```
- PPTX: `kind: "slide"`, every chunk on the cited slide in reading order, `label` "Slide N".
- DOCX/HTML: `kind: "section"`, the unbroken run of chunks sharing the cited chunk's section heading, at most 3 on each side. `label` is that heading (`null` for text outside any section), and the heading prefix the chunker adds to each chunk is removed from `content`.
- `content` is markdown for tables. `figure_url` is a 10-minute signed URL for figure chunks, else `null`.

**Errors:**
- `404 NOT_FOUND` — the document or chunk doesn't exist, isn't the caller's, or the chunk belongs to another document
- `422 VALIDATION_ERROR` — the document is a PDF (use the page image endpoint)

---

### `DELETE /documents/{document_id}`
Deletes the document's Storage objects (`uploads` file + `figures`), then the row. Chunks and citations cascade via FKs; the id is removed from any `conversations.document_ids` array in application code (arrays have no FK).

**Response 204:** empty body

**Errors:**
- `404 NOT_FOUND`
- `409 CONFLICT` — the document is still processing (`status in ('parsing', 'embedded')`; both phases still have background work in flight)
- `500 STORAGE_ERROR` — Storage removal failed; nothing was deleted, retrying is safe

---

### `POST /query`
Ask a question over one or more documents.

**Request:**
```json
{
  "question": "What was Q3 revenue?",
  "document_ids": ["3f9e...", "8a2c..."],
  "conversation_id": "optional-existing-conv-id",
  "k": 8,
  "rerank": false
}
```

- **`k`** — number of chunks retrieved, default 8, range 1–50.
- **`rerank`** — optional, default `false`. Enables Voyage `rerank-2.5` on the retrieved candidates. Adds one Voyage call and ~380ms; clients exposing it should default it off and disclose the cost.

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
      "association_method": "explicit",
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
- Runs hybrid retrieval → generation → verification pipeline (see ARCHITECTURE.md). In a continuing conversation, retrieval searches with the question rewritten into a standalone query (one extra Gemini Flash-Lite call, skipped when the question doesn't refer back to the conversation); generation answers the original question
- `verdict` is one of `supported` | `partial` | `unsupported` | `unverified` (see ARCHITECTURE.md §Verify flow):
  - `supported` — kept; the answer keeps its `[N]` marker.
  - `partial` — kept; the source backs only part of the claim (marker 3 above confirms broad growth but not "international demand"). Clients render it with a warning style.
  - `unsupported` — **dropped** from `citations`. A sentence whose citations were all unsupported is **removed** from `answer`, and `answer` ends with a note such as `_1 statement was removed because the cited source did not support it._`. If the sentence also cites a kept source, only the unsupported `[N]` marker is stripped. Still persisted for audit (`raw_content` keeps the original text).
  - `unverified` — kept, `supporting_quote` is always `null`. Verification itself could not run (Gemini error/timeout/malformed response) — never used as a substitute for `unsupported`. Clients must style it distinctly from both `supported` and `partial` (`components/chat/citation-marker.tsx`).
- **`figure_url`** — only on `element_type: "figure"` citations: a signed Storage URL valid for 600s, generated fresh on every read (live or historical). Omitted (not `null`) otherwise. If the figure fetch fails server-side the citation is downgraded to `element_type: "text"` with no `figure_url`.
- **`page_number`** — depends on the source format: a real page for PDF, a slide number for PPTX, and always `1` (no location available) for DOCX/HTML. Use `document_mime_type` to interpret it; `lib/chat/parse-message.ts`'s `citationLocation()` renders "Page N", "Slide N", or nothing.
- **`document_mime_type`** — the source document's `mime_type`.
- **`bbox`** — `{x0, y0, x1, y1}`, the cited chunk's area on its page in PDF points from the top-left corner. Omitted when the source has no real location (DOCX/HTML). Pass it to `GET /documents/{id}/pages/{n}/image` to show the highlighted page.
- **`association_method`** — only on `table`/`figure` citations whose caption was linked by the parser: `"explicit"` (parser's direct caption match), `"heuristic"` (chunker's weaker proximity match), or `"unmatched"`. Omitted otherwise. Not yet displayed by the frontend.
- `chunks.embedding_provider` is deliberately **not** exposed: which vendor located a chunk says nothing about whether the claim is supported — every citation goes through the same verifier.

**Errors:**
- `403 FORBIDDEN` if any `document_ids` don't belong to user
- `422 VALIDATION_ERROR` if `document_ids` empty or `question` empty
- `502 GENERATE_FAILED` if the Gemini generation call fails. Transient failures (429/5xx/network) are retried first, up to 3 attempts in total, waiting at most 8s per retry
- `404 NOT_FOUND` if `conversation_id` doesn't exist or isn't the caller's
- `429 RATE_LIMITED` — **3/minute** and **40/day** per user, one combined counter shared with `POST /query/stream`. Each call uses one Voyage query embedding (shared 3 RPM ceiling), one Gemini 3.6 Flash call, and Gemini 3.5 Flash-Lite verification calls. Reasoning in `routes/query.py`.

---

### `POST /query/stream`
SSE streaming variant of `POST /query` — same request body, same auth/ownership/history validation (run to completion **before** the stream opens, so an invalid `document_id`/JWT/conversation always comes back as a normal JSON error response with the codes above, never as a stream that starts and then errors out). A separate route rather than a mode flag on `/query`: `response_model=QueryResponse` validation and a `StreamingResponse` are mutually exclusive in FastAPI, and `/query`'s synchronous contract stays untouched for any caller that doesn't want SSE.

Browsers' `EventSource` can't send a POST body or an `Authorization` header — clients must use `fetch()` with a manually-read stream (see `apps/web/lib/api/query.ts`'s `askQuestionStream()`), not `EventSource`.

**Rate limiting:** shares `POST /query`'s counters. A limited request gets a normal JSON `429` before the stream opens.

**Response:** `Content-Type: text/event-stream`, one `event: <type>\ndata: <json>\n\n` frame per event, in this fixed order:

```
retrieving -> token* (zero or more) -> verifying -> citations-resolved -> done
```

`error` can replace any step from `token` onward and always terminates the stream — there is no path that closes the connection without either a `done` or an `error`.

| Event | `data` shape | Meaning |
|---|---|---|
| `retrieving` | `{}` | Retrieval has started. |
| `token` | `{"text": "..."}` | One raw text delta from Gemini, in arrival order. May contain unresolved `[N]` citation brackets — **must render as plain inert text, never styled or clickable**, since verification hasn't run yet. |
| `verifying` | `{}` | Generation is complete; citation verification has started. Claim-span extraction needs the full answer text, so this can never start earlier — there is no way to verify progressively. |
| `citations-resolved` | `{"conversation_id", "message_id", "answer", "citations"}` | Same `citations` shape as `POST /query`'s response (including the `supported`/`partial`/`unverified`-kept, `unsupported`-dropped-and-marker-stripped rule). `answer` is the **final**, marker-stripped text — clients should replace whatever raw text they'd accumulated from `token` events with this value, then re-parse citation markers against `citations` (see `buildAssistantMessage()`, reused for both the streaming and historical-message paths). |
| `done` | `{"metadata"}` | Same `metadata` shape as `POST /query`'s response. Terminal — the connection closes after this. |
| `error` | `{"code", "message"}` | One of `RETRIEVE_FAILED`, `GENERATE_FAILED`, `VERIFY_FAILED`, `PERSIST_FAILED`. Terminal; nothing is persisted. |

**UI contract for the gap between `token` and `citations-resolved`:** the client must show a distinct "verifying" indicator during this window — never let the fully-streamed-but-unverified text just sit there with no sign anything is still happening (`apps/web/components/chat/loading-stages.tsx`).

**Keepalive:** during any gap (retrieval, waiting for the first token, verification) the server sends `: keepalive` SSE comment frames every 12s. Clients should ignore them.

**Disconnect / stop:** if the client closes the connection (tab closed, or `AbortController.abort()` for a user "stop"), the server detects it and aborts the turn — no messages or citations are persisted.

**Implementation note:** every blocking call in the SSE generator (`Retriever.retrieve`, `Verifier.verify_batch`, `fetch_generator_chunks`, `signed_figure_url`, `create_query_turn`) must run via `asyncio.to_thread(...)`. Calling one directly freezes the event loop, so already-yielded frames don't flush until the next yield.

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

### `POST /conversations/{conversation_id}/rename`
Renames a conversation. (POST, matching the action-route style of `/reindex`.)

**Request:**
```json
{ "title": "New conversation title" }
```

**Response 200:** the updated conversation (same shape as
`GET /conversations/{id}/messages`'s `conversation` object):
```json
{
  "id": "6c1a...",
  "title": "New conversation title",
  "document_ids": ["3f9e..."],
  "created_at": "2026-07-22T14:00:00Z",
  "updated_at": "2026-07-22T14:00:00Z"
}
```

**Behaviour:**
- `title` is trimmed; empty (after trim) or over 200 chars (matching
  `create_query_turn`'s own auto-generated-title truncation) is a `422
  VALIDATION_ERROR`.
- Does **not** bump `updated_at` — a rename is not new activity, so it shouldn't reorder the "Recent" list.

**Errors:**
- `404 NOT_FOUND` if the conversation doesn't exist or belongs to another
  user (identical response either way — no ownership oracle)
- `422 VALIDATION_ERROR` — see above

---

### `DELETE /conversations/{conversation_id}`
Deletes the conversation; its messages and citations cascade via FKs. No Storage cleanup is needed.

**Response 204**

**Errors:**
- `404 NOT_FOUND` if the conversation doesn't exist or belongs to another
  user (identical response either way)

---

### `GET /export/conversations`
Exports every conversation, message, and citation belonging to the caller in one file. **Source documents are not included** — users already have their originals; what can't be reconstructed is the conversation history and verification audit trail.

**Query params:**
- `format` — `json` (default) or `markdown`. Any other value returns FastAPI's default `422`.

**Response 200:** the raw file body (not JSON-enveloped, even for
`format=json` — the whole response body IS the export), with:
- `Content-Type`: `application/json` or `text/markdown; charset=utf-8`
- `Content-Disposition: attachment; filename="docify-export-<timestamp>.<ext>"`

JSON shape:
```json
{
  "exported_at": "2026-08-06T12:00:00.000000Z",
  "conversation_count": 1,
  "message_count": 2,
  "citation_count": 1,
  "conversations": [
    {
      "id": "6c1a...",
      "title": "What are the termination clauses?",
      "document_names": ["lease.pdf"],
      "created_at": "2026-08-01T10:00:00Z",
      "updated_at": "2026-08-01T10:00:05Z",
      "messages": [
        { "id": "...", "role": "user", "content": "What are the termination clauses?", "raw_content": null, "created_at": "...", "citations": [] },
        {
          "id": "...", "role": "assistant",
          "content": "The lease can be terminated with 30 days notice [1].",
          "raw_content": "The lease can be terminated with 30 days notice [1].",
          "created_at": "...",
          "citations": [
            {
              "marker": 1, "chunk_id": "...", "document_name": "lease.pdf",
              "page_number": 4, "element_type": "text",
              "claim_span": "terminated with 30 days notice",
              "claim_start": null, "claim_end": null,
              "verdict": "supported",
              "supporting_quote": "either party may terminate with 30 days written notice",
              "verifier_model": "gemini-3.5-flash-lite",
              "verified_at": "2026-08-01T10:00:05Z"
            }
          ]
        }
      ]
    }
  ]
}
```

**Completeness:** unlike every other citation-returning route, `citations` here is **unfiltered** — it includes `unsupported` citations, because an export exists to be the complete audit trail.

Markdown format: one file, one `##` section per conversation. Each message uses `raw_content` (so every marker, including stripped `unsupported` ones, stays visible) followed by a `> **Sources**` block listing each citation's verdict, quote, and location.

**Behaviour:**
- No pagination and no async job — a single user's history is a handful of indexed queries at this project's scale.
- Not rate-limited: read-only, no vendor API calls.

**Errors:**
- `422` — invalid `format` value (FastAPI default shape)

---

### `DELETE /account`
**Permanently deletes the caller's entire account** — every document, chunk, conversation, message, citation, and usage counter; every Storage object under `{user_id}/` in `uploads`, `figures`, and `avatars`; and the `auth.users` row. See `routes/account.py`'s module docstring for the full enumeration.

**Request:** no body. The client must reauthenticate first (current password via `signInWithPassword()`, plus a typed-email confirmation) — the same check email change uses. The server trusts the JWT like every other route.

**Response 204** — no body.

**Ordering:**
1. Remove every Storage object under `{user_id}/` in `uploads`, then `figures`, then `avatars`, listed via Storage's own `list()` (so objects with no matching row are still caught). Stops at the first failing bucket.
2. Only once all three are clean, delete the auth user via the admin API. All 6 user-scoped tables cascade.

Deleting the auth user first would make an interrupted cleanup unrecoverable — no one could authenticate as that user again to finish it. A failure in step 1 leaves the account fully intact and retry-safe.

**Already-issued tokens:** JWT verification is stateless, so an unexpired access token still verifies until it expires; every query it makes simply matches no rows. Refresh tokens are revoked immediately by the admin delete.

**Not rate-limited** — no vendor API calls.

**Errors:**
- `401 UNAUTHORIZED` — missing/invalid JWT (standard middleware behavior)
- `500 STORAGE_ERROR` — a bucket's Storage removal failed; nothing was
  deleted, retrying is safe
- `500 DELETE_FAILED` — the final `auth.admin.delete_user()` call
  failed after Storage was already cleaned; DB rows are NOT yet
  deleted (the cascade never ran), retrying is safe

---

## Not-yet-defined endpoints

- `PATCH /documents/{id}` — rename a document
- An endpoint to delete or replace a message (needed for an in-place "regenerate"; today regenerate appends a new turn)

---

## Contract version

- Current: `v0.1`. The original plan was to freeze at `v1.0` once the frontend shipped; that milestone has passed (frontend deployed 2026-08-09) but the version has not been bumped. Until it is, breaking changes are still allowed but must update `apps/web/lib/types/` in the same PR and get a CHANGELOG entry.

## Personal workspaces (2026-10-06)

Authenticated endpoints: `GET /workspaces` returns `{workspaces: [{id, name, created_at, document_count}]}`. `POST /workspaces` with `{name}` creates one (201); `PATCH /workspaces/{id}` renames it; `DELETE /workspaces/{id}` returns 204. Names are trimmed, 1�60 characters, without control characters, unique per user ignoring case. Maximum 20 workspaces. A missing/foreign workspace returns 404. Delete returns 409 for the only workspace or a workspace with documents; an empty workspace's conversations cascade.

`POST /ingest` accepts optional `workspace_id`, defaulting to the user's oldest workspace. `GET /documents` and `GET /conversations` accept optional UUID `workspace_id`; omitted means all owned workspaces, preserving existing clients. Document, conversation list and conversation detail responses include `workspace_id`.

Both query endpoints reject document sets spanning workspaces, or documents outside an existing conversation's workspace, with 422. Retrieval stays scoped by authenticated user and explicit document IDs. Workspaces are private per user; no team membership or invitations.
