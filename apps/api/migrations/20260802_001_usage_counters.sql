-- 20260802_001_usage_counters.sql
--
-- Postgres-backed daily rate-limit counters, replacing slowapi's
-- in-memory storage for the DAILY (not per-minute) limits specifically.
--
-- Reasoning (FEAT-024 follow-up, .agent/SCOPE.md's Phase 5 "Production
-- job execution" entry): Render's free tier loses ALL in-memory state on
-- every ~15-minute idle spin-down (a fresh container process starts on
-- the next request). slowapi's in-memory counters reset along with it.
-- For a per-minute limit this is a non-issue -- a 15-minute idle gap
-- already exceeds any per-minute window, so "the window reset" and "the
-- process restarted" are indistinguishable in practice. For a DAILY
-- limit it's a real gap: a user could, in principle, ride out a spin-down
-- (or several) over the course of a day and get a fresh 10/day or 40/day
-- budget on each restart, silently multiplying their real daily quota
-- against the shared Voyage/Gemini vendor ceilings this limit exists to
-- protect. Moving daily counters to Postgres closes that gap; per-minute
-- limits stay in slowapi's in-memory storage (rate_limit.py) since a
-- restart-induced reset there was never materially different from a
-- legitimate window rollover.
--
-- One row per (user_id, route, day) -- "route" is a plain text label
-- ("ingest", not a URL path), matching query.py's own shared_limit scope
-- convention (/query and /query/stream already share one counter under
-- one scope string; /ingest and /reindex share one counter under
-- "ingest" the same way, since /reindex draws on the identical real
-- Voyage/Gemini budget -- FEAT-024-follow-up task, Part 3 item 4).

create table if not exists usage_counters (
  user_id  uuid not null references auth.users(id) on delete cascade,
  route    text not null,
  day      date not null,
  count    int not null default 0,
  primary key (user_id, route, day)
);

-- No user-facing access needed -- this is server-side rate-limit
-- bookkeeping only, written and read exclusively by rate_limit.py via
-- the service-role client. RLS enabled with zero policies means only
-- service_role (BYPASSRLS) can touch it, matching this project's
-- existing pattern for internal-only state.
alter table usage_counters enable row level security;

-- RLS alone does not grant access -- service_role has BYPASSRLS, but
-- BYPASSRLS and base table GRANTs are independent permission layers in
-- Postgres (confirmed the hard way in 20260722_002_grant_table_
-- privileges.sql: the very first migration in this project hit exactly
-- this gap on every table). No policies are defined here since nothing
-- but service_role should ever touch this table at all.
grant select, insert, update on usage_counters to service_role;

-- Atomic increment-then-return, single round trip -- avoids a real
-- read-modify-write race between two concurrent requests from the same
-- user hitting /ingest or /reindex at nearly the same moment (a plain
-- SELECT-then-UPDATE from Python would have a real TOCTOU gap; this
-- pushes the increment and the conflict-resolution into one atomic
-- Postgres statement instead).
create function increment_usage_counter(
  p_user_id uuid,
  p_route text,
  p_day date
)
returns int
language sql
as $$
  insert into usage_counters (user_id, route, day, count)
  values (p_user_id, p_route, p_day, 1)
  on conflict (user_id, route, day)
  do update set count = usage_counters.count + 1
  returning count;
$$;

revoke execute on function increment_usage_counter(uuid, text, date) from public;
grant execute on function increment_usage_counter(uuid, text, date) to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop function if exists increment_usage_counter(uuid, text, date);
-- drop table if exists usage_counters;
