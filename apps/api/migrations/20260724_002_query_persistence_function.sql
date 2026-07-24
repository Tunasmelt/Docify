-- 20260724_002_query_persistence_function.sql
--
-- FEAT-012 (/query endpoint) must create a conversations row (if new),
-- two messages rows (user question + assistant answer), and N citations
-- rows atomically — "atomically" per this feature's own acceptance
-- criteria. PostgREST's REST layer has no cross-table transaction
-- primitive: separate .insert() calls from app code cannot be made
-- atomic from the client side. The standard, already-established way to
-- do this through Supabase's REST layer is a single Postgres function
-- called via `client.rpc(...)` — the same reasoning FEAT-009 used for
-- hybrid search (a single PL/pgSQL function body is one implicit
-- transaction; either everything below commits, or an exception rolls
-- the whole thing back).
--
-- All of retrieve() -> generate() -> verify() happens in Python BEFORE
-- this function is ever called — if any of those fail, /query returns
-- an error and this function is never invoked, so nothing partial is
-- ever written. This function's only job is the final, all-or-nothing
-- persistence step once every upstream result already exists.
--
-- p_conversation_id is trusted to already belong to p_user_id by the
-- time this is called — /query pre-checks ownership with a plain scoped
-- SELECT (matching FEAT-008's get_document() pattern) before calling
-- this function, since that gives a clean 404 without needing custom
-- Postgres-exception-code handling in Python. The `where ... and
-- user_id = p_user_id` on the UPDATE below is defense-in-depth against
-- a TOCTOU race between that pre-check and this call, not the primary
-- enforcement — matching FEAT-009's "the explicit WHERE clause is the
-- real boundary" pattern, just with the roles of pre-check/RPC reversed
-- from that migration (there, the RPC's WHERE was primary; here, the
-- route's pre-check is primary and this WHERE is the backstop).
--
-- EXECUTE is revoked from PUBLIC and granted only to service_role, same
-- discipline as 20260724_001 — this is only ever called from the
-- service-role client (which bypasses RLS entirely), never directly by
-- an authenticated/anon client.

create or replace function create_query_turn(
  p_user_id uuid,
  p_conversation_id uuid,
  p_document_ids uuid[],
  p_question text,
  p_answer_content text,
  p_answer_raw_content text,
  p_retrieved_chunk_ids uuid[],
  p_answer_metadata jsonb,
  p_citations jsonb
)
returns table (conversation_id uuid, message_id uuid)
language plpgsql
as $$
declare
  v_conversation_id uuid;
  v_message_id uuid;
  v_citation jsonb;
begin
  if p_conversation_id is null then
    insert into conversations (user_id, title, document_ids)
    values (p_user_id, left(p_question, 200), p_document_ids)
    returning id into v_conversation_id;
  else
    update conversations
    set updated_at = now()
    where id = p_conversation_id and user_id = p_user_id
    returning id into v_conversation_id;

    -- Only reachable if the route's own pre-check raced with a delete —
    -- the route already returned 404 for a conversation that never
    -- belonged to this user. A NULL here means genuinely unexpected
    -- state, not a normal client error, so the caller treats this as a
    -- 500, not a 404 (the 404 path was already handled before this
    -- function was ever invoked).
    if v_conversation_id is null then
      raise exception 'create_query_turn: conversation % not found for user % at persistence time',
        p_conversation_id, p_user_id;
    end if;
  end if;

  insert into messages (conversation_id, user_id, role, content)
  values (v_conversation_id, p_user_id, 'user', p_question);

  insert into messages (
    conversation_id, user_id, role, content, raw_content, retrieved_chunk_ids, metadata
  )
  values (
    v_conversation_id, p_user_id, 'assistant', p_answer_content, p_answer_raw_content,
    p_retrieved_chunk_ids, p_answer_metadata
  )
  returning id into v_message_id;

  -- Every verified (claim, chunk) pair is persisted here regardless of
  -- verdict — including 'unsupported' — for a full audit trail (same
  -- discipline as messages.raw_content storing the pre-verification
  -- answer). /query's own response to the client separately filters
  -- this down to supported/partial only; that filtering is a read-time
  -- concern, not a write-time one, so nothing here needs to know about it.
  for v_citation in select * from jsonb_array_elements(p_citations)
  loop
    insert into citations (
      message_id, chunk_id, user_id, claim_span, claim_start, claim_end,
      verdict, supporting_quote, verifier_model
    )
    values (
      v_message_id,
      (v_citation ->> 'chunk_id')::uuid,
      p_user_id,
      v_citation ->> 'claim_span',
      (v_citation ->> 'claim_start')::int,
      (v_citation ->> 'claim_end')::int,
      (v_citation ->> 'verdict')::verdict,
      v_citation ->> 'supporting_quote',
      v_citation ->> 'verifier_model'
    );
  end loop;

  return query select v_conversation_id, v_message_id;
end;
$$;

revoke execute on function create_query_turn(uuid, uuid, uuid[], text, text, text, uuid[], jsonb, jsonb) from public;
grant execute on function create_query_turn(uuid, uuid, uuid[], text, text, text, uuid[], jsonb, jsonb) to service_role;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- drop function if exists create_query_turn(uuid, uuid, uuid[], text, text, text, uuid[], jsonb, jsonb);
