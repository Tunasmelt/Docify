/** Wire shape of a citation as returned by both POST /query and
 * GET /conversations/{id}/messages (API_CONTRACT.md: "same shape as in
 * POST /query") — kept as one shared type so the two response parsers
 * (lib/api/query.ts, lib/api/conversations.ts) can never silently
 * diverge on what a citation looks like. `marker` is the real,
 * persisted `[N]` position (FEAT-026) — never re-derived client-side. */
export interface ApiCitation {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  /** The source document's real mime_type (FEAT-020, 2026-07-27) —
   * needed to know what `page_number` actually means: a real PDF page,
   * a PPTX slide index, or a DOCX/HTML `page_number: 1` sentinel that
   * must never be displayed as if it were a real location
   * (.agent/SCHEMA.md's page_number note). See lib/chat/parse-message.ts's
   * `citationLocation()` for the one place this gets turned into display
   * text. */
  document_mime_type: string;
  page_number: number;
  element_type: string;
  snippet: string;
  verdict: "supported" | "partial" | "unsupported";
  supporting_quote: string | null;
  /** Present only on a figure citation (API_CONTRACT.md, FEAT-026) —
   * absent entirely (not `null`) on text/table citations, per
   * `response_model_exclude_none=True` on both routes. */
  figure_url?: string;
}
