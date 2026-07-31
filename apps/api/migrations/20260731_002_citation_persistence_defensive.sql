-- 20260731_002_citation_persistence_defensive.sql
--
-- Real production incident: a streaming /query turn (a broad "summarize the
-- provided paper" request) failed with "null value in column 'marker' of
-- relation 'citations' violates not-null constraint." create_query_turn is
-- ONE atomic plpgsql function — the citation INSERT loop runs AFTER the
-- user-question and assistant-answer message rows are already inserted in
-- the SAME function body, so any exception anywhere in that loop rolls back
-- the ENTIRE function, including those two message inserts. One malformed
-- citation was silently losing the whole conversation turn (the answer text
-- included), directly matching a separate report that chat history
-- "doesn't persist."
--
-- Extensive live reproduction (apps/api/routes/query.py's real
-- _extract_claim_spans + Generator._parse_citations, exercised against 6+
-- real Gemini-generated broad-summarization answers across single-turn,
-- multi-topic, and conversation-history-carrying follow-up shapes, plus 8
-- hand-constructed adversarial answer shapes fed directly through the real
-- parsing functions) did not find a live path that produces a null marker
-- in the CURRENT Python code — that logic already drops a citation whose
-- bracket has no resolvable claim-bearing sentence (e.g. a trailing
-- standalone "[N]" after the final period) rather than persisting it with a
-- null marker. But "we couldn't reproduce it today" is not the same as "it
-- cannot happen" — Python-side prevention is necessary but not sufficient
-- for a system this project has adversarially audited more than any other
-- for exactly this reason (silent citation corruption is much harder to
-- notice than a crash). This migration is the structural, defense-in-depth
-- fix: make the DB layer itself robust to a malformed citation, independent
-- of whether the Python layer is ever proven airtight.
--
-- Fix: each citation INSERT is now wrapped in its own nested
-- BEGIN/EXCEPTION block. A citation that fails for ANY reason (null/
-- non-numeric marker, a chunk_id that doesn't cast to uuid, an invalid
-- verdict enum value, or anything else) is logged via RAISE WARNING and
-- skipped — the loop continues to the next citation rather than aborting
-- the whole function. The message rows (already inserted above the loop)
-- and every other valid citation in the same turn commit normally. This is
-- the exact same "fail this one thing safely, never the whole turn"
-- principle FEAT-010's hallucinated-marker handling already established at
-- the Python layer, now also enforced where the actual data-loss occurred.

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
    begin
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
    exception when others then
      -- One malformed citation must never cost the rest of the turn — the
      -- message rows above and every other citation in this same loop
      -- still commit. Logged loudly (message_id + the raw offending JSON)
      -- so a real occurrence is visible in Postgres logs, not silently
      -- swallowed.
      raise warning 'create_query_turn: skipped one malformed citation for message % (% : %) — raw: %',
        v_message_id, sqlstate, sqlerrm, v_citation;
    end;
  end loop;

  return query select v_conversation_id, v_message_id;
end;
$$;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- Restore the pre-defensive function body from 20260725_002_query_persistence_function_marker.sql
-- (identical signature — CREATE OR REPLACE is a true replace here, same
-- param list, same RETURNS TABLE shape, so no DROP FUNCTION is needed to
-- roll back, unlike a signature-changing migration).
