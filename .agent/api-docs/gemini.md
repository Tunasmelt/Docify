# Google Gemini API

**Verified:** 2026-07-24 (models/pricing section: 2026-07-22 — unchanged, re-confirmed; request/response shape section below is new)
**Docs:** https://ai.google.dev/gemini-api/docs
**SDK:** `google-genai` 1.7.0 (installed, `apps/api/.venv`) — verified against installed source directly (`google/genai/models.py`, `types.py`, `client.py`), not from memory.

---

## Models in use

| Role | Model ID | Notes |
|---|---|---|
| Generation | `gemini-3.6-flash` | Current flagship — "frontier intelligence with superior search and grounding," more token-efficient than 3.5 Flash (~17% fewer output tokens on comparable tasks). Multimodal (text, image, video, audio, PDF). Replaces Claude Sonnet for `/query` generation. |
| Verification (LLM-as-judge) | `gemini-3.5-flash-lite` | Fastest/cheapest current model, optimized for high-throughput, low-latency tasks (agentic search, document processing). Multimodal. Replaces Claude Haiku for citation verification. |
| OCR fallback | `gemini-2.5-flash` | **Unchanged.** Already in use for low-confidence Docling pages (see ARCHITECTURE.md). Left on 2.5 — no reason to churn a working, already-free-tier-covered path just because 3.x exists. |

## Pricing (paid tier, per 1M tokens)

| Model | Input | Output |
|---|---|---|
| `gemini-3.6-flash` | $1.50 | $7.50 |
| `gemini-3.5-flash-lite` | $0.30 | $2.50 (batch: $0.15 / $1.25) |

## Source

- [Gemini API models overview](https://ai.google.dev/gemini-api/docs/models)
- [gemini-3.6-flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)
- [gemini-3.5-flash-lite model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
- [Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Introducing Gemini 3.6 Flash, 3.5 Flash-Lite, and 3.5 Flash Cyber (Google blog)](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-6-flash-3-5-flash-lite-3-5-flash-cyber/)

## Notes for implementers

- SDK: `@google/genai` (JS) or `google-genai` (Python) — replaces `anthropic` SDK in `apps/api/pyproject.toml` for generation/verification. Voyage and Docling deps unaffected.
- Env var: `GEMINI_API_KEY` (already present for OCR fallback — reuse, no new credential needed).
- Free tier rate limits were not published on the fetched pages as of this check — confirm actual RPD/RPM before relying on free tier for generation load; re-run `/api-check gemini` before implementation if this doc is >30 days old.

## Request/response shape (FEAT-010, verified against installed SDK source, not docs prose)

**Client construction** — the SDK's automatic env-var detection looks for `GOOGLE_API_KEY`, NOT `GEMINI_API_KEY` (this project's actual env var name, shared with the OCR fallback path) — `api_key` must be passed explicitly:
```python
from google import genai
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
```

**Generation call** (`google/genai/models.py:5293`, sync client):
```python
response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents=[...],                        # flat list[Part] — SDK wraps as one user Content
    config=types.GenerateContentConfig(
        system_instruction="...",           # plain str accepted (ContentUnion)
        temperature=0.2,
    ),
)
```

**Multimodal `contents`** — a flat `list[types.Part]`, text and inline image parts freely interleaved in order (`types.py:554`):
```python
types.Part.from_text(text="...")
types.Part.from_bytes(data=png_bytes, mime_type="image/png")   # in-memory bytes, no upload/URI needed for our figure PNGs
```
`Part.from_uri(file_uri=..., mime_type=...)` exists for GCS/Files-API-hosted content — not used here since figure images come from Supabase Storage as bytes already in hand, not a `gs://` URI.

**Response shape** (`types.py:2885`):
- `response.text` — concatenated text of the first candidate (property, `None` if no candidates/content/parts)
- `response.model_version` — actual model version string the API used (report this as `metadata.model`, not the requested model string — it's what the server actually ran)
- `response.usage_metadata.prompt_token_count` / `.candidates_token_count` / `.total_token_count` (`types.py:2843`) — all `Optional[int]`

**Errors** (`google/genai/errors.py`): `APIError` (base) → `ClientError` (4xx) / `ServerError` (5xx). No SDK-internal retry (unlike `voyageai.Client(max_retries=...)`) — confirmed by inspecting `client.py`/`types.py` for a retry-options field; none exists. A hand-rolled retry was judged out of scope for FEAT-010 per its "standard-depth, no new trust boundary" framing — errors are wrapped and surfaced, not retried.

## Structured output / `response_schema` (FEAT-011, verified against installed SDK source — applies to the whole `generate_content` API surface, not a `gemini-3.5-flash-lite`-specific shape, and confirmed live against Flash-Lite specifically before relying on it)

Used instead of free-text parsing for the citation verifier, since FEAT-010's `[N]`-marker regex needed three separate rounds of fixes (grouped brackets, delimiters, sign handling) precisely because it parsed free text — a schema-constrained response closes that whole failure class here rather than re-deriving the same lesson.

```python
import pydantic

class MyResponse(pydantic.BaseModel):
    field: str

config = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=MyResponse,   # a pydantic.BaseModel subclass — accepted directly (SchemaUnion = dict | type | Schema | ...)
)
response = client.models.generate_content(model=MODEL, contents=contents, config=config)
parsed: MyResponse | None = response.parsed
```

**Critical failure-mode detail, easy to miss** (`types.py:3018-3046`, `GenerateContentResponse._from_response`): if the model's JSON doesn't parse or doesn't validate against the schema, the SDK catches `pydantic.ValidationError`/`json.decoder.JSONDecodeError` **internally and silently** (`except ...: pass`) rather than raising — `response.parsed` is simply left `None`. Code that assumes `response.parsed` is populated whenever the API call itself succeeds will crash on `None` access, or worse, silently treat a malformed response as some falsy-but-valid state. Always explicitly check `response.parsed is None` as its own failure branch, separate from and in addition to catching `APIError` around the call itself.

## Embeddings (`gemini-embedding-2`) — verified live 2026-07-31, services/embedder.py's Voyage fallback

**Docs:** https://ai.google.dev/gemini-api/docs/embeddings

**Model:** `gemini-embedding-2` (also `gemini-embedding-2-preview`, `gemini-embedding-001`). **Use `gemini-embedding-2` specifically, not `-001`** — two real, live-confirmed differences that matter for this project:

| | `gemini-embedding-001` | `gemini-embedding-2` |
|---|---|---|
| Accepts image `Part` content | **No** — real call returns `400 INVALID_ARGUMENT: The text content is empty` when sent an image | **Yes** — confirmed live with both an image-only `Content` and a mixed text+image `Content` |
| `output_dimensionality=1024` truncation result | NOT unit-normalized (real norm ≈0.61–0.62, varies by input) | Unit-normalized (real norm ≈1.0000, consistent) |

**Auth: the plain `GEMINI_API_KEY`/`genai.Client(api_key=...)` path works — NOT Vertex AI/service-account auth**, despite that historically being Google's pattern for multimodal embeddings. Confirmed live: the exact same client construction this project's OCR tier (`services/parser.py`) already uses successfully calls `gemini-embedding-2` with real image content. No new credential needed for a Gemini embedding fallback.

**Call shape** (`Client.models.embed_content`, confirmed live with the full real shape together — task_type + output_dimensionality + multimodal content + batching, not each piece verified in isolation):
```python
response = client.models.embed_content(
    model="gemini-embedding-2",
    contents=[
        types.Content(parts=[
            types.Part.from_text(text="a description"),
            types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
        ]),
        # additional Content entries batch natively in one call — confirmed
        # live, multiple entries return multiple embeddings, in order, via
        # the same batchEmbedContents endpoint models.py routes through
    ],
    config={
        "task_type": "RETRIEVAL_DOCUMENT",   # or "RETRIEVAL_QUERY" for query-side — same
                                              # asymmetric embedding pattern as Voyage's
                                              # input_type="document"/"query"
        "output_dimensionality": 1024,
    },
)
vectors = [list(e.values) for e in response.embeddings]  # one per `contents` entry, in order
```

**Response shape:** `response.embeddings` — a list, one entry per `contents` item, each with `.values` (the float vector). The installed SDK's `embed_content` docstring says "Only text is supported" — this is stale for `gemini-embedding-2`/`-2-preview` (confirmed live to accept images); it's accurate for `-001`.

**Free tier rate limit:** 100 RPM / 1,000 RPD (per public reporting, not independently hit-live the way Voyage's 3 RPM and `gemini-2.5-flash`'s 20 RPD were — re-verify if this becomes a real bottleneck) — far less constrained than Voyage's real, empirically-observed 3 RPM ceiling on this project's account, which is the entire reason this is viable as a fallback target.

**Do not compare a `gemini-embedding-2` vector to a `voyage-multimodal-3.5` vector via cosine similarity, even at matching 1024 dimensionality** — they are different, unrelated embedding spaces. See `.agent/MEMORY.md`'s 2026-07-31 standing anti-pattern entry.
