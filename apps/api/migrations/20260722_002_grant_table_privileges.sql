-- 20260722_002_grant_table_privileges.sql
--
-- Fixes a real gap found doing a LIVE enforcement test (not just a static
-- policy-presence check) against a local `supabase start` instance: this
-- database's default privileges for the `public` schema deliberately
-- exclude SELECT/INSERT/UPDATE/DELETE for anon/authenticated/service_role
-- (only TRUNCATE/REFERENCES/TRIGGER are granted by default). RLS policies
-- only filter which ROWS a role can see/touch — the role still needs the
-- base table-level GRANT underneath, and 20260722_001_initial.sql never
-- added one. Even service_role, which has BYPASSRLS, was blocked by this:
-- RLS bypass has nothing to do with table-level ACLs, they're independent
-- layers. This almost certainly affects the live project too, since
-- nothing had ever actually attempted a write against these tables before.
--
-- Grants below mirror exactly the RLS policies already defined in
-- 20260722_001_initial.sql — `authenticated` gets only the operations a
-- policy exists for; anything beyond that is still blocked at the RLS
-- layer even with the grant. `service_role` gets full CRUD on everything,
-- consistent with SCHEMA.md's "service-role bypasses RLS by design."
-- `anon` gets nothing — this project requires a JWT for all non-/health
-- endpoints (API_CONTRACT.md), so anon has no legitimate reason to touch
-- any of these tables directly.

grant select, insert, update, delete on documents to authenticated;
grant select, insert, update, delete on documents to service_role;

grant select on chunks to authenticated;
grant select, insert, update, delete on chunks to service_role;

grant select, insert, update, delete on conversations to authenticated;
grant select, insert, update, delete on conversations to service_role;

grant select on messages to authenticated;
grant select, insert, update, delete on messages to service_role;

grant select on citations to authenticated;
grant select, insert, update, delete on citations to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- revoke select, insert, update, delete on documents from authenticated, service_role;
-- revoke select, insert, update, delete on chunks from authenticated, service_role;
-- revoke select, insert, update, delete on conversations from authenticated, service_role;
-- revoke select, insert, update, delete on messages from authenticated, service_role;
-- revoke select, insert, update, delete on citations from authenticated, service_role;
