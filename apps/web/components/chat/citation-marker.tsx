"use client";

import * as React from "react";

import { CITATION_VERDICT_STYLES } from "@/lib/status-styles";
import type { Citation } from "@/lib/types/chat";

export interface CitationMarkerProps {
  citation: Citation;
  active: boolean;
  onOpen: (citation: Citation) => void;
}

const EXCERPT_PREVIEW_MAX_CHARS = 160;

function truncateExcerpt(text: string): string {
  return text.length > EXCERPT_PREVIEW_MAX_CHARS ? `${text.slice(0, EXCERPT_PREVIEW_MAX_CHARS)}…` : text;
}

/** The inline `[N]` superscript button — the through-line motif from
 * the onboarding footnote treatment into real cited claims. Verdict
 * drives color: solid green for supported, amber with a dotted
 * underline for partial, muted with a dashed underline for unverified
 * (2026-08-03 — distinct from partial's dotted-amber: this isn't a
 * warning about the content being wrong, it's "we couldn't check this
 * one" — see CITATION_VERDICT_STYLES). Never renders unsupported —
 * those are dropped server-side before reaching the client, per
 * API_CONTRACT.md. Hover shows a quick-glance excerpt preview (batch 1,
 * item 7, chat UI modernization) — click still opens the full
 * source-panel, unchanged. */
export function CitationMarker({ citation, active, onOpen }: CitationMarkerProps) {
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const style = CITATION_VERDICT_STYLES[citation.verdict];
  const isPartial = citation.verdict === "partial";
  const isUnverified = citation.verdict === "unverified";
  // location is null for DOCX/HTML sources (no real page concept —
  // FEAT-020) — omit the location clause entirely rather than show a
  // false "p. 1" for every citation in the document.
  const locationSuffix = citation.location
    ? `, ${citation.location.kind === "page" ? "Page" : "Slide"} ${citation.location.number}`
    : "";
  const tipPrefix = isPartial ? "Partially supported — " : isUnverified ? "Could not be verified — " : "";
  const tip = tipPrefix + `${citation.documentName}${locationSuffix}`;

  return (
    <sup className="relative">
      <button
        type="button"
        data-testid={`citation-marker-${citation.id}`}
        data-verdict={citation.verdict}
        onClick={() => onOpen(citation)}
        onMouseEnter={() => setPreviewOpen(true)}
        onMouseLeave={() => setPreviewOpen(false)}
        onFocus={() => setPreviewOpen(true)}
        onBlur={() => setPreviewOpen(false)}
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
          borderBottom: isPartial
            ? `1px dotted ${style.fg}`
            : isUnverified
              ? `1px dashed ${style.fg}`
              : "none",
        }}
      >
        {citation.n}
      </button>
      {previewOpen ? (
        // Absolutely positioned relative to the <sup> itself — never
        // affects text flow/layout, so it can't push or collide with
        // the streaming cursor (item 3) that may sit right after a
        // citation near the end of an in-progress message; it only
        // exists on hover/focus, floating above surrounding content.
        // z-20 keeps it above normal message text but below the source
        // panel (z-40) and the copy-message button — neither of which
        // it would ever overlap anyway.
        <span
          data-testid={`citation-preview-${citation.id}`}
          role="tooltip"
          className="absolute bottom-full left-1/2 z-20 mb-1.5 w-[240px] -translate-x-1/2 animate-fade-up rounded-md border border-border bg-surface p-2.5 text-left normal-case leading-normal shadow-[0_8px_24px_rgba(25,23,20,0.16)]"
        >
          <span
            className="mb-1.5 block truncate font-mono text-[10px] font-medium tracking-[0.04em]"
            style={{ color: style.fg }}
          >
            {citation.documentName}
            {locationSuffix}
          </span>
          <span className="block font-serif text-[13px] leading-snug text-muted">
            {truncateExcerpt(citation.excerpt)}
          </span>
        </span>
      ) : null}
    </sup>
  );
}
