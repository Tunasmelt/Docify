"use client";

import { CITATION_VERDICT_STYLES } from "@/lib/status-styles";
import type { Citation } from "@/lib/types/chat";

export interface CitationMarkerProps {
  citation: Citation;
  active: boolean;
  onOpen: (citation: Citation) => void;
}

/** The inline `[N]` superscript button — the through-line motif from
 * the onboarding footnote treatment into real cited claims. Verdict
 * drives color: solid green for supported, amber with a dotted
 * underline for partial (never renders unsupported — those are
 * dropped server-side before reaching the client, per
 * API_CONTRACT.md). */
export function CitationMarker({ citation, active, onOpen }: CitationMarkerProps) {
  const style = CITATION_VERDICT_STYLES[citation.verdict];
  const isPartial = citation.verdict === "partial";
  const tip =
    (isPartial ? "Partially supported — " : "") +
    `${citation.documentName}, p. ${citation.page}`;

  return (
    <sup>
      <button
        type="button"
        onClick={() => onOpen(citation)}
        title={tip}
        className="min-w-[15px] rounded-[3px] px-[3px] font-mono text-[0.74em] font-medium transition-colors"
        style={{
          color: style.fg,
          background: active ? style.bg : "transparent",
          borderBottom: isPartial ? `1px dotted ${style.fg}` : "none",
        }}
      >
        {citation.n}
      </button>
    </sup>
  );
}
