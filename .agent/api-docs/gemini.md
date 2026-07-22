# Google Gemini API

**Verified:** 2026-07-22
**Docs:** https://ai.google.dev/gemini-api/docs

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
