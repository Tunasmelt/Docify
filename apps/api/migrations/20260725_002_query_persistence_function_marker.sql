-- 20260725_002_query_persistence_function_marker.sql
--
-- Companion to 20260725_001_citation_marker_column.sql: `create_query_turn`
-- (20260724_002) already receives p_citations as a jsonb array where each
-- element is the same dict routes/query.py builds in `citations_to_persist`
-- — this just extracts one more key (`marker`, now present in that dict as
-- of the accompanying Python change) the same way every other field already
-- is. No new function parameter, no signature change — same reasoning as
-- why this doesn't need its own REVOKE/GRANT statements (the function
-- identity, and therefore its existing grants, is unchanged).

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

  for v_citation in select * from jsonb_array_elements(p_citations)
  loop
    insert into citations (
      message_id, chunk_id, user_id, marker, claim_span, claim_start, claim_end,
      verdict, supporting_quote, verifier_model
    )
    values (
      v_message_id,
      (v_citation ->> 'chunk_id')::uuid,
      p_user_id,
      (v_citation ->> 'marker')::int,
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

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- Restore the pre-marker function body from 20260724_002_query_persistence_function.sql.
