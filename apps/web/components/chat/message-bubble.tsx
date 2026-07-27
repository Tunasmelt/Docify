"use client";

import { CitationMarker } from "@/components/chat/citation-marker";
import type { AssistantMessage, Citation, UserMessage } from "@/lib/types/chat";

export function UserMessageBubble({ message }: { message: UserMessage }) {
  return (
    <div className="flex justify-end">
      <div
        data-testid="user-message"
        className="max-w-[70%] rounded-[12px_12px_4px_12px] bg-panel-active px-4 py-3 text-[15px] leading-relaxed"
      >
        {message.text}
      </div>
    </div>
  );
}

export interface AssistantMessageBubbleProps {
  message: AssistantMessage;
  activeCitationId: string | null;
  onOpenCitation: (citation: Citation) => void;
}

export function AssistantMessageBubble({
  message,
  activeCitationId,
  onOpenCitation,
}: AssistantMessageBubbleProps) {
  const citationsById = Object.fromEntries(message.citations.map((c) => [c.id, c]));

  return (
    <div data-testid="assistant-message" className="max-w-[85%]">
      <p className="m-0 text-[15px] leading-[1.75]">
        {message.segments.map((seg, i) => {
          if (seg.type === "text") return <span key={i}>{seg.text}</span>;
          const citation = citationsById[seg.citationId];
          if (!citation) return null;
          return (
            <CitationMarker
              key={i}
              citation={citation}
              active={activeCitationId === citation.id}
              onOpen={onOpenCitation}
            />
          );
        })}
      </p>
      {message.citations.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-x-[18px] gap-y-1 border-t border-line pt-2">
          {message.citations.map((citation) => (
            <button
              key={citation.id}
              type="button"
              onClick={() => onOpenCitation(citation)}
              className="flex items-center gap-1 border-none bg-transparent p-0 font-mono text-[11px] tracking-[0.02em] text-faint hover:text-muted"
            >
              <span
                style={{
                  color:
                    citation.verdict === "partial" ? "var(--amber)" : "var(--accent)",
                }}
              >
                {citation.n}
              </span>
              {citation.documentName}
              {/* location is null for DOCX/HTML sources (no real page
                  concept — FEAT-020) — omit rather than show a false
                  page number for every citation in the document. */}
              {citation.location
                ? ` · ${citation.location.kind === "page" ? "P" : "S"}.${citation.location.number}`
                : ""}
              {citation.verdict === "partial" ? " · partial" : ""}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
