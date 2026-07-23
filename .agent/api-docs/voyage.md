# Voyage AI API

**Verified:** 2026-07-23
**Docs:** https://docs.voyageai.com/docs/multimodal-embeddings, https://docs.voyageai.com/docs/embeddings

---

## Model in use

`voyage-multimodal-3.5` (per `.agent/ARCHITECTURE.md`'s locked embeddings decision) — this project's stack table names 3.5; Voyage's public docs describe `voyage-multimodal-3`'s limits, which the project treats as the same numbers apply to 3.5 (same model family, no evidence of a different limit for the .5 revision as of this check).

## Input limits (verified via two independent lookups, consistent)

| Limit | Value |
|---|---|
| Max tokens per single input | **32,000 tokens** |
| Max total tokens per batch request | 320,000 tokens |
| Max inputs per batch request | 1,000 |
| Image token counting | every 560 pixels = 1 token |
| Max image size | 20 MB or 16 million pixels |
| Max video size | 20 MB |

## Notes for implementers

- **FEAT-005 (chunker) hard size ceiling is set well under the 32,000/input limit** — not close to it. A single chunk approaching 32,000 tokens would be a huge fraction of a batch's 320,000-token total budget on its own. The ceiling exists to guarantee the chunker never hands FEAT-006 something that could plausibly hit the real 32,000 limit, not to hug it.
- **Token counting mismatch — now actually measured, not just assumed.** chunker.py approximates tokens as `len(text) // 4` (no tokenizer dependency). Voyage's SDK ships a real, local tokenizer that doesn't require an API call: `voyageai.Client(...).tokenizer("voyage-multimodal-3").encode_batch(texts)` (downloads a HF tokenizer file once, works fully offline after). Ran this against every chunk produced from `clean_digital.pdf` and `table_heavy.pdf` (46 chunks, mixed table markdown and body text): **mean proxy/real ratio 0.94** (the char/4 proxy is, on average, very close to Voyage's real count, slightly under) — but **individual chunks ranged from 0.43x to 2.31x**, i.e. the proxy can be off by more than 2x in either direction on any single chunk, even though it's accurate in aggregate. Worst observed underestimate (the dangerous direction for a size ceiling) was ~2.33x — even at that rate, `MAX_CHUNK_TOKENS=4000` (proxy) corresponds to at most ~9,300 real tokens, still comfortably under the real 32,000 limit (~29% of it in the worst case measured). **Conclusion: the 4,000-token proxy ceiling has enough margin to hold even given the proxy's measured worst-case error — FEAT-006 does not need its own separate defensive size check on top of this, based on what's been measured so far.** If FEAT-006 ever sees a real Voyage rejection for exceeding input length despite this ceiling, that would mean the error is worse in production data than in these two fixtures — re-run this same measurement against whatever document triggered it before assuming the ceiling needs to move.
- SDK: `voyageai` (Python), already a pinned dependency in `apps/api/pyproject.toml`.
- Env var: `VOYAGE_API_KEY` (already present in `.env`/`.env.example`).

## Source

- [Multimodal Embeddings](https://docs.voyageai.com/docs/multimodal-embeddings)
- [Text Embeddings](https://docs.voyageai.com/docs/embeddings)
