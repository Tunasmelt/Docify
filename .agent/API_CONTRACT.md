# API Contract

Specification for the internal FastAPI endpoints. Both `apps/api` (Pydantic models) and `apps/web` (TS types) must conform to this document. Changes require a CHANGELOG entry and a version bump if breaking.

**Scope:** internal endpoints only. External API references (Voyage, Gemini, Supabase, Docling) live under `.agent/api-docs/` and are populated by `/api-check`.

---

## Base URL

- Development: `http://localhost:8000`
- Production: `https://docify-api.onrender.com` (placeholder — update after Render deploy)

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
| `VALIDATION_ERROR` | 422 | Request body failed schema validation |
| `RATE_LIMITED` | 429 | Free-tier ceiling hit on a downstream API |
| `PARSE_FAILED` | 500 | Docling could not parse the document |
| `EMBED_FAILED` | 502 | Voyage API call failed |
| `GENERATE_FAILED` | 502 | Gemini API call failed |
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
  "status": "parsing",
  "created_at": "2026-07-22T14:30:00Z"
}
```

**Behaviour:**
- Creates `documents` row with `status='uploaded'`, returns `202` immediately
- Downloads file, parses with Docling, chunks, embeds, inserts chunks — this happens in a background task (FastAPI BackgroundTasks for v1; queue system if scale demands)
- Client polls `GET /documents/{id}` or subscribes via Supabase Realtime to observe status transitions

**Errors:**
- `403 FORBIDDEN` if `storage_path` does not start with `uploads/{jwt.user_id}/`
- `422 VALIDATION_ERROR` if mime_type unsupported
- Background failures write `documents.error` and set `status='failed'`; no synchronous error surface

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
- `409 CONFLICT` if document is currently being parsed

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
  "answer": "Q3 revenue was $4.2M [1], up 18% year-over-year [2].",
  "citations": [
    {
      "marker": 1,
      "chunk_id": "b2e0...",
      "document_id": "3f9e...",
      "document_name": "annual-report-2025.pdf",
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
      "page_number": 14,
      "element_type": "table",
      "snippet": "| Q2 | $3.56M | | Q3 | $4.20M |",
      "verdict": "supported",
      "supporting_quote": "Q3 $4.20M"
    }
  ],
  "metadata": {
    "model": "gemini-3.6-flash",
    "verifier_model": "gemini-3.5-flash-lite",
    "retrieved_count": 8,
    "cited_count": 2,
    "latency_ms": 3420
  }
}
```

**Behaviour:**
- If `conversation_id` omitted, creates a new conversation
- If `conversation_id` provided, appends to it (must belong to user)
- Runs hybrid retrieval → generation → verification pipeline (see ARCHITECTURE.md)
- Any citation with `verdict: 'unsupported'` is dropped from the returned array; the corresponding marker in the answer text is stripped

**Errors:**
- `403 FORBIDDEN` if any `document_ids` don't belong to user
- `422 VALIDATION_ERROR` if `document_ids` empty or `question` empty
- `502 GENERATE_FAILED` if Gemini call fails after retries

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
