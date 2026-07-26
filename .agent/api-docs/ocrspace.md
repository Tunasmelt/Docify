# OCR.space REST API

**Verified:** 2026-07-26, live against the real API (not docs prose alone) — `.agent/api-docs/gemini.md`'s pattern doesn't fit here since this is a plain REST endpoint, no SDK.
**Docs:** https://ocr.space/OCRAPI

## Request

```
POST https://api.ocr.space/parse/image
Headers:  apikey: <key>          # NOT a body/query param — a header
Body (form-encoded):
  base64Image: "data:image/png;base64,<...>"   # used here — a PIL page image in hand, no upload/URL needed
  OCREngine: 2                                  # engine 2, not the default (1) — see note below
```

`file` (multipart) and `url` (remote fetch) forms also exist but aren't used — a rendered page image is already in memory (same one Gemini's tier gets), so `base64Image` is the only form that avoids either a temp file or a publicly-reachable URL.

## Response

```json
{
  "OCRExitCode": 1,
  "IsErroredOnProcessing": false,
  "ErrorMessage": null,
  "ParsedResults": [
    { "FileParseExitCode": 1, "ParsedText": "...", "ErrorMessage": null }
  ]
}
```

Recognized text: `ParsedResults[0]["ParsedText"]`. Failure signal: check `IsErroredOnProcessing` (top-level) — a truthy value here means treat the whole call as failed regardless of HTTP status (200 OK is returned even for a processing failure, per FEAT-017-tier2's live testing). `OCRExitCode` values: 1 success, 2 partial (multi-page), 3/4 failure.

## Engine choice

`OCREngine=2` — the newer, more accurate engine on OCR.space's own free-tier docs; engine 1 (the default if omitted) is the older/lower-accuracy one. No reason to accept the weaker default when the parameter to opt into the better one costs nothing.

## Demo key

`helloworld` — publicly documented, no registration needed, 500 requests/day per IP. Confirmed live (2026-07-26): a real page image through this exact key returned `IsErroredOnProcessing: false` and legible recovered text. Used for this project's real-tier-2 test; the real project key (`OCR_SPACE_API_KEY`) is expected to replace it in `.env` for anything beyond ad hoc verification, same discipline as every other credential here (never committed).

## Auth failure shape

Confirmed live (2026-07-26, audit follow-up): an invalid `apikey` comes back as a real `403 Forbidden` — caught by `raise_for_status()` (an `httpx.HTTPStatusError`), not by the `IsErroredOnProcessing` flag. So both checks are real, distinct failure paths, not redundant: bad auth → 4xx status; a genuine processing failure (unreadable image, unsupported format, etc.) → 200 OK with `IsErroredOnProcessing: true`. `OcrSpaceClient.transcribe_page()`'s single broad `except Exception` catches both uniformly (logs, returns `None`, chain moves to tier 3) — there's no SDK-provided error taxonomy to lean on here, unlike `google-genai`'s `ClientError`/`ServerError` hierarchy. Confirmed separately that the logged warning on this path never includes the actual key value, only the exception type/message and URL.
