# Voyage AI API

**Verified:** 2026-07-23 (FEAT-005: input limits only; FEAT-006: full request/response shape, auth, retry behavior, batching, verified against the installed SDK's source directly, not just docs)
**Docs:** https://docs.voyageai.com/docs/multimodal-embeddings, https://docs.voyageai.com/docs/embeddings, https://docs.voyageai.com/reference/multimodal-embeddings-api

---

## Model in use

`voyage-multimodal-3.5` (per `.agent/ARCHITECTURE.md`'s locked embeddings decision) — this project's stack table names 3.5; Voyage's public docs describe `voyage-multimodal-3`'s limits, which the project treats as the same numbers apply to 3.5 (same model family, no evidence of a different limit for the .5 revision as of this check).

**Output dimension:** 1024 by default (Matryoshka — also supports 256/512/1024/2048 via `output_dimension`). Matches `.agent/SCHEMA.md`'s `chunks.embedding vector(1024)` column. `apps/api/services/embedder.py` passes `output_dimension=1024` explicitly rather than relying on the default, so a future Voyage default change can't silently break the schema match.

## Input limits (verified via two independent lookups, consistent)

| Limit | Value |
|---|---|
| Max tokens per single input | **32,000 tokens** |
| Max total tokens per batch request | **320,000 tokens** |
| Max inputs per batch request | **1,000** |
| Image token counting | every 560 pixels = 1 token |
| Max image size | 20 MB or 16 million pixels |
| Max video size | 20 MB |

**Correction from FEAT-005's implementer notes:** an earlier guess assumed a 128-input batch cap. Checked the installed SDK's source directly (`voyageai/__init__.py`, `voyageai/embeddings_utils.py`) — `VOYAGE_EMBED_BATCH_SIZE = 128` exists, but it's only used by a separate legacy text-only convenience helper (`embeddings_utils.py`'s `default_chunk_fn`), **not** by `Client.multimodal_embed()`, which enforces no client-side batch cap at all. The real constraint on `multimodal_embed()` is purely server-side: 1,000 inputs **and** 320,000 total tokens per call, per the docs above. For realistic chunk sizes (~500 tokens typical, up to 4,000-proxy/~9,300-real worst case per FEAT-005), **the 320,000-total-token limit is the binding constraint before the 1,000-input count is**, e.g. 1,000 chunks averaging 500 real tokens each would be 500,000 total tokens — over the limit despite being under the input-count cap. `embedder.py` batches against both.

## Auth (verified from SDK source, `voyageai/util.py`)

- Header: `Authorization: Bearer <VOYAGE_API_KEY>` — standard bearer auth, nothing project-specific.
- The SDK reads `VOYAGE_API_KEY` from the environment automatically if `voyageai.Client()` is constructed with no explicit `api_key` — `embedder.py` still passes it explicitly (via constructor injection, matching the pattern already used in `middleware/auth.py`/`db/client.py`) for consistency and testability, not because the SDK requires it.

## Request/response shape (verified from SDK source, `voyageai/object/multimodal_embeddings.py`)

**How text and image combine in one input:** each "input" to `multimodal_embed()` is a **list of segments** — a single input can mix text strings and images in any order, e.g. `["Figure 3: a bar chart", pil_image]` is one input with two segments, embedded as one combined vector. This is the "one input = a list of `[str | PIL.Image.Image, ...]`" form the SDK accepts directly (there's also a lower-level dict-of-`{"type": "text"|"image_base64"|..., ...}` form; the list-of-mixed-items form is simpler and is what `embedder.py` uses).

**Image encoding:** the SDK converts `PIL.Image.Image` objects to base64-encoded lossless WEBP automatically (`MultimodalInputRequest._image_to_base64`, converts to RGB first). `embedder.py` never touches image bytes directly — it hands the SDK a live `PIL.Image.Image` and the SDK does the rest. This matters for FEAT-004's image-ownership contract: `embedder.py` reads a chunk's image but does not modify or close it.

**Effective request shape** (what the SDK sends, for reference — `embedder.py` never constructs this JSON directly, the SDK does):
```json
{
  "inputs": [
    {"content": [{"type": "text", "text": "..."}, {"type": "image_base64", "image_base64": "data:image/webp;base64,..."}]}
  ],
  "model": "voyage-multimodal-3.5",
  "input_type": "document",
  "output_dimension": 1024
}
```

**Response object** (`MultimodalEmbeddingsObject`, what `Client.multimodal_embed()` returns):
- `.embeddings: list[list[float]]` — one vector per input, same order as the request.
- `.text_tokens`, `.image_pixels`, `.video_pixels`, `.total_tokens: int` — usage accounting for the call.

**`input_type`:** `None` (raw, no prefix), `"query"` (prepends "Represent the query for retrieving supporting documents: "), or `"document"` (prepends "Represent the document for retrieval: "). `embedder.py` (ingestion-side, embedding chunks for storage) uses `"document"`. The query-side embed call (FEAT-009, retriever) is a separate concern and should use `"query"` when it's built — not addressed here.

## Retry behavior (verified from SDK source, `voyageai/_client.py`'s `_make_retry_controller`)

**The SDK already implements retry with exponential backoff + jitter natively** — no need to hand-roll one. `voyageai.Client(max_retries=N)` (default `max_retries=0`, i.e. no retry unless set) wraps every call in `tenacity.Retrying(stop=stop_after_attempt(N), wait=wait_exponential_jitter(initial=1, max=16), retry=...)`. The retry predicate covers exactly three exception types:
- `RateLimitError` (429)
- `ServiceUnavailableError`
- `Timeout`

All other `voyageai.error.VoyageError` subtypes (`AuthenticationError`, `InvalidRequestError`, `MalformedRequestError`, `APIConnectionError`, `ServerError`, `VideoProcessingError`) are **not** retried by the SDK — they're treated as non-transient and raise immediately. `embedder.py` constructs its client with `max_retries=3` (per FEATURES.md's acceptance criterion) and maps the non-retried exception types to `EmbedError`.

## Source

- [Multimodal Embeddings](https://docs.voyageai.com/docs/multimodal-embeddings)
- [Text Embeddings](https://docs.voyageai.com/docs/embeddings)
- [Multimodal Embeddings API reference](https://docs.voyageai.com/reference/multimodal-embeddings-api)
- Installed SDK source (`apps/api/.venv/Lib/site-packages/voyageai/`, version pinned in `apps/api/pyproject.toml`) — authoritative for request/response shape, retry behavior, and the (non-)existence of a client-side batch cap, since these weren't fully covered by the fetched docs pages.
