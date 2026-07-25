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
        data-testid={`citation-marker-${citation.id}`}
        data-verdict={citation.verdict}
        onClick={() => onOpen(citation)}
        title={tip}
        // Tailwind's Preflight reset sets `sup { line-height: 0 }` (the
        // standard typographic sub/sup reset) — this button inherits
        // that (Preflight also resets `button { line-height: inherit }`),
        // collapsing it to zero height and making it genuinely
        // unclickable in a real browser despite looking fine in a static
        // screenshot. leading-[1.4] overrides the inherited 0 directly on
        // the element, restoring real, clickable height. Caught live via
        // Playwright (`element is not visible`, computed height: 0px) —
        // not visible from reading the JSX/CSS alone.
        className="min-w-[15px] rounded-[3px] px-[3px] font-mono text-[0.74em] font-medium leading-[1.4] transition-colors"
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
