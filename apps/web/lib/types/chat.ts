export type CitationVerdict = "supported" | "partial";

export interface Citation {
  id: string;
  /** Position marker as shown in the answer text, e.g. `[1]` — 1-indexed
   * per chunk, matching the backend's citation-marker contract
   * (API_CONTRACT.md). */
  n: number;
  documentName: string;
  /** Null when the source format has no real page/location concept at
   * all (DOCX/HTML — Docling gives no page boundary for these; the
   * backend's `page_number` is an honest but meaningless `1` sentinel
   * that must never be displayed as if it were real, FEAT-020,
   * .agent/SCHEMA.md). "page" for PDF (a real PDF page). "slide" for
   * PPTX (`page_number` really is the slide index for this format).
   * Computed once in lib/chat/parse-message.ts's `citationLocation()`
   * from the API's `document_mime_type` — every display site reads this
   * instead of re-deriving the format/omit decision itself. */
  location: { kind: "page" | "slide"; number: number } | null;
  verdict: CitationVerdict;
  excerpt: string;
  isFigure?: boolean;
  figureCaption?: string;
  /** Signed Storage URL for a figure citation's image (API_CONTRACT.md,
   * FEAT-026) — absent on non-figure citations and on a figure citation
   * whose image fetch failed server-side. Never persisted client-side
   * beyond the current render; a fresh one is fetched on every reload. */
  figureUrl?: string;
}

export type MessageSegment =
  | { type: "text"; text: string }
  | { type: "citation"; citationId: string };

export interface UserMessage {
  id: string;
  role: "user";
  text: string;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  segments: MessageSegment[];
  citations: Citation[];
}

export type ChatMessage = UserMessage | AssistantMessage;
