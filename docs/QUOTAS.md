# Provider quotas and spend boundary

Docify's production throughput is bounded by provider-account quotas, not only by application code. These figures are observations from this project's current accounts and may change when provider tiers or policies change.

| Provider path | Observed ceiling | Application behavior |
|---|---:|---|
| Voyage multimodal embeddings | 3 requests/minute without a billing method | Server-guided retries; per-user and global ingest limits; Gemini embedding fallback after Voyage retries are exhausted. |
| Gemini generation and verification | Free-tier, model-specific limits | Query routes are rate-limited; infrastructure failures are surfaced or recorded as `unverified` rather than silently treated as supported. |
| Gemini OCR (`gemini-2.5-flash`) | 20 requests/day per project/model | Falls through to OCR.space, then local Tesseract. A daily exhaustion is not recoverable by short backoff. |
| OCR.space | Account/IP-dependent | Used only after Gemini OCR fails; final fallback is local Tesseract when available. |

## Retry semantics

- A `Retry-After` duration means the caller can retry after that temporary window.
- A daily quota exhaustion should be described as unavailable until the provider's daily reset; repeated immediate retries only waste work.
- Tesseract has no vendor quota but requires the system binary and may produce lower-quality OCR.

## Spend decision

Raising these ceilings requires adding billing or moving provider tier. That is a product/spend decision, not a software defect. Before enabling a paid tier, record the expected users, documents per day, average pages, query volume, and a monthly spend cap. Keep live-provider quality tests manual so CI does not consume production quota.

## Deferred operational work

A durable worker queue, Redis-backed shared rate limiting, generalized metrics platform, and non-PDF source viewers remain deferred until concurrency, restarts, support incidents, or horizontal scaling demonstrate the need.

## Cohere fallback (2026-10-06)

Optional COHERE_API_KEY enables Gemini → Cohere Command A for answers, rewriting and citation verification. Embedding order: Voyage → Gemini → Cohere embed-v4.0 (1024 dimensions). Each provider is searched in its own vector space; failed query embeddings allow keyword search to continue. Streaming switches only before any text is sent. Cohere retries 429/5xx/network failures up to three attempts, with 1s/2s backoff and an 8s maximum accepted Retry-After. Command A receives text evidence, not figure images; OCR retains its existing chain.
