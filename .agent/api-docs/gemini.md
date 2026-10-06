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
| OCR fallback | `gemini-2.5-flash` | **Unchanged.** Tier 1 of the OCR fallback chain for low-yield PDF pages (see ARCHITECTURE.md). Left on 2.5 — no reason to churn a working, already-free-tier-covered path just because 3.x exists. |

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

- SDK: `@google/genai` (JS) or `google-genai` (Python) — replaces `anthropic` SDK in `apps/api/pyproject.toml` for generation/verification. Voyage deps unaffected.
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

### Real input/batch limits — verified live 2026-08-02, not inherited from Voyage's

This fallback was originally built without checking whether Gemini's real limits match what `services/chunker.py` (Voyage-derived `MAX_CHUNK_TOKENS=4,000`) and `services/embedder.py`'s batching (Voyage-derived `MAX_INPUTS_PER_BATCH=1,000`, `_SAFE_TOTAL_TOKENS_PER_BATCH=300,000`) already guarantee for Voyage. They do NOT match. Real numbers, confirmed against the live API, not just Google's docs:

| | Voyage (`voyage-multimodal-3.5`) | Gemini (`gemini-embedding-2`) |
|---|---|---|
| Per-input token limit | 32,000 (`.agent/api-docs/voyage.md`) | **8,192** — 4x tighter |
| Per-batch request count | 1,000 | **100** — 10x tighter |
| Over-limit behavior | (not applicable — chunker.py's ceiling has huge margin) | **Silent truncation, no error** |

**Per-input: 8,192 tokens.** Google's own docs state this; confirmed live two ways: (1) `client.models.count_tokens(model="gemini-embedding-2", contents=[...])` — a real, separate API call that reports the exact real token count for any content, with no embedding side-effect — and (2) by directly observing the failure mode (below). **Exceeding it does NOT raise an error — Gemini silently truncates the input.** Proven live, not just per docs: a real 32,000-char chunk (built from this project's own real fixture text, repeated — the fixtures don't contain 32,000 distinct real chars) measured at 13,195 real tokens (60% over the limit) via `count_tokens`, then embedded successfully via `embed_content` with no error. `cosine(embedding of the full over-limit input, embedding of an independently-submitted ~8,192-token-equivalent prefix of the SAME text)` = **0.999999**. For contrast, `cosine(same full input, embedding of genuinely unrelated real text)` = **0.777**. A near-1.0 match against the truncated-equivalent prefix, that far above the "different content" baseline, is direct proof the extra ~5,000 tokens were dropped server-side with zero signal, not that the model "handled" them some other way.

A single chunk at `chunker.py`'s own `MAX_CHUNK_TOKENS=4,000` (proxy, char/4) ceiling — 16,000 real chars of the same real fixture-text mix — measured at **6,365 real Gemini tokens** (77.7% of the real 8,192 limit). Comfortably under it for this specific content, but with materially less margin than the ~29% worst-case utilization already verified against Voyage's real 32,000 limit (`.agent/FEATURES.md`'s FEAT-006 entry) — `MAX_CHUNK_TOKENS` was never re-validated against Gemini's tighter ceiling before this, and different (denser) real content could plausibly exceed it.

**Per-batch: exactly 100 requests.** Google's own docs: `"BatchEmbedContentsRequest.requests: at most 100 requests can be in one batch"`. Confirmed live at the EXACT boundary: 100 real inputs in one `batchEmbedContents` call succeeded; 101 failed immediately with a clean `400 INVALID_ARGUMENT`. This is a real, previously-live bug, not a theoretical one: the fallback used to hand Gemini whatever batch Voyage's own real limits had already assembled (up to 1,000 chunks) — any Voyage batch above 100 chunks that fell back to Gemini would have hard-failed the ENTIRE fallback outright, even though Gemini could handle the same chunks fine once correctly re-batched. Fixed in `services/embedder.py` (`_batch_for_gemini`, `GEMINI_MAX_INPUTS_PER_BATCH=100`) — the fallback now re-batches against Gemini's own real limit before sending anything. No separate, lower aggregate-token cap was found up to ~119,000 real tokens across 50 real inputs in the same live testing — the 100-request cap is the actual binding constraint for typical chunk sizes, not a total-token budget.

**Image tokenization: a flat, constant 258 tokens per image, regardless of size.** Confirmed live across 13 distinct real sizes via `count_tokens` — every one from 1x1 through 8000x6000 returned exactly 258. This is NOT Voyage's documented pixels/560 formula (confirmed not to transfer: two real fixture figures at 300x200 and 400x300, which Voyage's formula predicts at 107 and 214 tokens respectively, both measured a real, identical 258 via Gemini). **It is also NOT the size-dependent tiled formula Google's own image-understanding docs describe** (flat 258 tokens for images with both dimensions ≤384px; larger images tiled at up to ~1,548 tokens for a 960x540 example) — that formula is real, but for a GENERATION model's image understanding (e.g. `gemini-2.5-flash`), not this embedding endpoint. A first attempt at implementing Gemini's image-token estimate assumed the tiled formula applied here and was wrong — caught by a second live measurement (this project's own real 400x300 fixture figure measuring 258, not the tiled formula's predicted 1,032) before it shipped. **Lesson generalized:** even Google's own official docs describing "how Gemini tokenizes images" can describe the wrong endpoint's behavior for a specific real call — always confirm against the actual endpoint being used, not just a docs page that mentions the right company/technology.

**Fix summary (`services/embedder.py`, `_embed_batch_with_gemini_fallback`):** (1) re-batches against the real 100-request cap before sending anything to Gemini; (2) a conservative local per-input token estimate (calibrated against the real measurements above — deliberately pessimistic, no extra API call per chunk) flags and logs any chunk at real risk of Gemini's silent truncation, converting an invisible failure mode into a visible one, even though it isn't (and can't cheaply be, without duplicating `chunker.py`'s own splitting logic) prevented outright. `chunker.py`'s `MAX_CHUNK_TOKENS` itself was NOT lowered — it doesn't need to shrink for Voyage's own much larger real limit, and the targeted fix belongs at the point where Gemini is actually used, not as a blanket restriction on every provider.
