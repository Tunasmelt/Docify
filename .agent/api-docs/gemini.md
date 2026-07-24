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
