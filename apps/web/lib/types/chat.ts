export type CitationVerdict = "supported" | "partial";

export interface Citation {
  id: string;
  /** Position marker as shown in the answer text, e.g. `[1]` — 1-indexed
   * per chunk, matching the backend's citation-marker contract
   * (API_CONTRACT.md). */
  n: number;
  documentName: string;
  page: number;
  verdict: CitationVerdict;
  excerpt: string;
  isFigure?: boolean;
  figureCaption?: string;
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
