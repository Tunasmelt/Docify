-- 20260725_001_citation_marker_column.sql
--
-- FEAT-026 (GET /conversations, GET /conversations/{id}/messages) found a
-- real gap while building the messages endpoint: `citations` has no column
-- recording which inline `[N]` marker a citation corresponded to in the
-- generated answer. POST /query's response includes `marker` (FEAT-012,
-- models/query.py's CitationResponse), but it was never persisted — it's
-- computed fresh each request from Generator's in-memory
-- `GenerateResult.cited_indices` (routes/query.py's `position` loop
-- variable) and simply never made it into `create_query_turn`'s
-- `p_citations` payload.
--
-- This matters beyond just "the field is missing": a message's stored
-- `content` text keeps the ORIGINAL, non-renumbered `[N]` markers (see
-- routes/query.py's `_strip_dropped_markers` — it removes dropped
-- positions but never renumbers the survivors), so any historical-message
-- endpoint reconstructing `marker` by re-numbering the kept citations
-- sequentially (1, 2, 3, ...) would silently mismatch whatever `[N]` the
-- stored text actually contains whenever an earlier citation was dropped
-- (unsupported). Silently wrong citation numbers in a citation-verification
-- product is worse than an endpoint that doesn't exist yet — so this is
-- fixed at the source (persist the real value) rather than worked around
-- at read time.

alter table citations add column marker int not null default 0;
alter table citations alter column marker drop default;

comment on column citations.marker is
  'The [N] position this citation corresponds to in the message content text at generation time (POST /query''s CitationResponse.marker) — NOT a display-order re-numbering. Existing rows before this migration backfilled to 0 (pre-launch dev data only, no real conversations exist yet).';

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- alter table citations drop column marker;
