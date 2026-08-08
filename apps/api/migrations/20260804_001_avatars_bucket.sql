-- 20260804_001_avatars_bucket.sql
--
-- Settings, batch 1, item 2 — a real Storage bucket for profile avatars.
--
-- PUBLIC-READ, deliberately different from the existing `uploads`/
-- `figures` buckets (both private, RLS-scoped to `auth.uid()` on
-- SELECT too): those hold real, potentially confidential document
-- content — a law firm's actual case files — while an avatar is a
-- small image the user chose to represent themselves, categorically
-- lower sensitivity, the same distinction most real products draw
-- (GitHub/Slack/etc. all serve avatars via a plain public URL even for
-- otherwise-private accounts). Public-read also avoids needing a
-- signed-URL refresh mechanism (services/figure_fetcher.py's pattern)
-- everywhere identity is rendered (sidebar, Settings) — a stable public
-- URL just works in a plain <img src>.
--
-- Write access (INSERT/UPDATE/DELETE) IS RLS-scoped to the owner, same
-- folder-prefix-equals-auth.uid() pattern as the `uploads` bucket
-- (20260722_001_initial.sql) — the real security boundary for a public
-- bucket is "can anyone overwrite/delete MY avatar," not "can anyone
-- see it." One fixed object per user (`{user_id}/avatar`, no filename
-- variability) rather than a folder of historical uploads — a new
-- upload REPLACES the previous one via `upsert: true` client-side,
-- matching a profile picture's real "one current avatar" semantics
-- rather than accumulating orphaned old files.

insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;

drop policy if exists avatars_insert on storage.objects;
create policy avatars_insert on storage.objects for insert
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists avatars_update on storage.objects;
create policy avatars_update on storage.objects for update
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists avatars_delete on storage.objects;
create policy avatars_delete on storage.objects for delete
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

-- SELECT policy, open to any bucket_id='avatars' row (no owner
-- restriction) — found necessary by REAL testing, not assumed: without
-- this, `upload(..., { upsert: true })` (the client's own re-upload
-- path, lib/supabase/profile.ts) failed with "new row violates
-- row-level security policy" on every upload past the first. Root
-- cause, confirmed live: `public = true` on the bucket only bypasses
-- RLS for the separate object-content-serving HTTP endpoint
-- (/storage/v1/object/public/...); the storage.objects METADATA table
-- (what upload/upsert/list actually operate on) is still governed by
-- its own RLS regardless of the bucket's public flag. Postgres's
-- INSERT ... ON CONFLICT DO UPDATE (upsert's real implementation) needs
-- to be able to SELECT the conflicting row to resolve the conflict at
-- all — with zero SELECT policy, that lookup was itself blocked,
-- breaking upsert even though the INSERT and UPDATE policies alone were
-- individually correct. Not a privacy loosening: content is already
-- public via the bucket flag, so allowing metadata SELECT too reveals
-- nothing additional.
drop policy if exists avatars_select on storage.objects;
create policy avatars_select on storage.objects for select
  using (bucket_id = 'avatars');

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop policy if exists avatars_insert on storage.objects;
-- drop policy if exists avatars_update on storage.objects;
-- drop policy if exists avatars_delete on storage.objects;
-- delete from storage.buckets where id = 'avatars';
