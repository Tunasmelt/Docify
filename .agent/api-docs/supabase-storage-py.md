# Supabase Storage — Python client (storage3), signed URLs

Verified: 2026-07-25
Installed: `storage3` (bundled with `supabase-py`), checked directly against installed source —
`.venv/Lib/site-packages/storage3/_sync/file_api.py` — since the project's cached `supabase.md`
doc only covers the JS Auth client (a different package, different language), not Storage/Python.

## `create_signed_url`

```python
client.storage.from_("figures").create_signed_url(
    path: str,          # object path within the bucket, e.g. "{user_id}/{document_id}/{chunk_index}.png"
    expires_in: int,     # seconds until the URL expires — fully caller-controlled, no client-side cap
    options: Optional[URLOptions] = None,  # {download, transform} — neither needed for a plain view
) -> SignedUrlResponse  # {"signedURL": str | None, "signedUrl": str | None} — same value, both keys present
```

Synchronous (`_sync`), matching every other Storage call already in this codebase
(`figure_fetcher.py`'s `.download()`, `documents.py`'s `.remove()` — none of them `await`).

## Why this is needed at all

`figures` bucket is `public: false` (`migrations/20260722_001_initial.sql`) — the only
`storage.objects` policy on it is a `select` policy scoped to `(storage.foldername(name))[1] =
auth.uid()::text`, usable only by a request carrying the OWNING user's own JWT. The backend's
service-role client (used for all reads/writes in `routes/`) bypasses RLS entirely and has no
JWT to hand the frontend — a signed URL is the only way to grant the frontend temporary,
scoped read access to a specific figure object without a full authenticated API round-trip
(and without the corresponding privacy loss of making the bucket public).

## Expiry choice for citation figure URLs (FEAT-026)

**600 seconds (10 minutes).** No project precedent existed to match (first use of signed URLs
anywhere in this codebase — checked via grep). Reasoning: long enough that a user reading a
`/query` or `/conversations/{id}/messages` response and clicking through a citation's figure
doesn't hit a race against a expiring URL mid-read; short enough that a leaked/logged URL isn't
a long-lived credential. Not a hard requirement from any spec — easy to tune later if real usage
shows it's wrong in either direction.
