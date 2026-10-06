-- 20261007_002_workspaces.sql
--
-- Personal workspaces: a user can keep several separate sets of documents and
-- conversations. A workspace belongs to exactly one user (no sharing). Chunks,
-- messages and citations inherit their workspace through their document or
-- conversation, so only documents and conversations carry workspace_id.
--
-- Backward compatibility: workspace_id may be omitted on insert. A trigger
-- fills it, so the API's old call shapes and direct inserts keep working:
--   * a document gets the user's default workspace;
--   * a conversation gets the workspace of its first document, else the default.
-- The default workspace is the user's oldest one, created ("My workspace") on
-- first need by default_workspace_id().

create table if not exists workspaces (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null check (char_length(name) between 1 and 60),
  created_at  timestamptz not null default now()
);

create unique index if not exists workspaces_user_name_uniq on workspaces (user_id, lower(name));
create index if not exists workspaces_user_idx on workspaces (user_id, created_at);

alter table workspaces enable row level security;

create policy workspaces_select on workspaces for select using (auth.uid() = user_id);
create policy workspaces_insert on workspaces for insert with check (auth.uid() = user_id);
create policy workspaces_update on workspaces for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy workspaces_delete on workspaces for delete using (auth.uid() = user_id);

grant select, insert, update, delete on workspaces to authenticated;
grant select, insert, update, delete on workspaces to service_role;

-- The user's oldest workspace, created if they have none. security definer so
-- the triggers below work whichever role performs the insert.
create or replace function default_workspace_id(p_user_id uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
begin
  select id into v_id from workspaces where user_id = p_user_id order by created_at, id limit 1;
  if v_id is null then
    insert into workspaces (user_id, name) values (p_user_id, 'My workspace')
    on conflict (user_id, lower(name)) do nothing;
    select id into v_id from workspaces where user_id = p_user_id order by created_at, id limit 1;
  end if;
  return v_id;
end;
$$;

revoke execute on function default_workspace_id(uuid) from public;
grant execute on function default_workspace_id(uuid) to service_role;

-- Backfill: one workspace per user who has data, and everything they own goes in it.
insert into workspaces (user_id, name)
select distinct user_id, 'My workspace' from (
  select user_id from documents union select user_id from conversations
) owners
on conflict (user_id, lower(name)) do nothing;

alter table documents add column if not exists workspace_id uuid references workspaces(id);
alter table conversations add column if not exists workspace_id uuid references workspaces(id) on delete cascade;

update documents d set workspace_id = default_workspace_id(d.user_id) where d.workspace_id is null;
update conversations c set workspace_id = coalesce(
  (select d.workspace_id from documents d where d.id = any(c.document_ids) and d.user_id = c.user_id limit 1),
  default_workspace_id(c.user_id)
) where c.workspace_id is null;

alter table documents alter column workspace_id set not null;
alter table conversations alter column workspace_id set not null;

create index if not exists documents_workspace_idx on documents (user_id, workspace_id, created_at desc);
create index if not exists conversations_workspace_idx on conversations (user_id, workspace_id, updated_at desc);

create or replace function documents_default_workspace()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.workspace_id is null then
    new.workspace_id := default_workspace_id(new.user_id);
  end if;
  if not exists (select 1 from workspaces where id = new.workspace_id and user_id = new.user_id) then
    raise exception 'workspace does not belong to document owner' using errcode = '23514';
  end if;
  return new;
end;
$$;

create or replace function conversations_default_workspace()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.workspace_id is null then
    select d.workspace_id into new.workspace_id
    from documents d
    where d.id = any(new.document_ids) and d.user_id = new.user_id
    limit 1;
    if new.workspace_id is null then
      new.workspace_id := default_workspace_id(new.user_id);
    end if;
  end if;
  if not exists (select 1 from workspaces where id = new.workspace_id and user_id = new.user_id) then
    raise exception 'workspace does not belong to conversation owner' using errcode = '23514';
  end if;
  if exists (
    select 1 from unnest(new.document_ids) doc_id
    left join documents d on d.id = doc_id
    where d.id is null or d.user_id <> new.user_id or d.workspace_id <> new.workspace_id
  ) then
    raise exception 'conversation documents must belong to its workspace' using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists documents_default_workspace on documents;
create trigger documents_default_workspace before insert or update of workspace_id, user_id on documents
  for each row execute function documents_default_workspace();

drop trigger if exists conversations_default_workspace on conversations;
create trigger conversations_default_workspace before insert or update of workspace_id, user_id, document_ids on conversations
  for each row execute function conversations_default_workspace();

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop trigger if exists conversations_default_workspace on conversations;
-- drop trigger if exists documents_default_workspace on documents;
-- drop function if exists conversations_default_workspace();
-- drop function if exists documents_default_workspace();
-- drop index if exists conversations_workspace_idx;
-- drop index if exists documents_workspace_idx;
-- alter table conversations drop column if exists workspace_id;
-- alter table documents drop column if exists workspace_id;
-- drop function if exists default_workspace_id(uuid);
-- drop table if exists workspaces;
