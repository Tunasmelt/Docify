"use client";

import * as React from "react";
import { createPortal } from "react-dom";

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
  const [position, setPosition] = React.useState<{ top: number; left: number } | null>(null);
  const anchor = React.useRef<HTMLButtonElement>(null);
  const preview = React.useRef<HTMLSpanElement>(null);
  const tooltipId = React.useId();
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
  React.useEffect(() => {
    if (!previewOpen) { setPosition(null); return; }
    const place = () => {
      if (!anchor.current || !preview.current) return;
      const button = anchor.current.getBoundingClientRect();
      const box = preview.current.getBoundingClientRect();
      const left = Math.max(8, Math.min(button.left + button.width / 2 - box.width / 2, window.innerWidth - box.width - 8));
      const above = button.top - box.height - 8;
      const top = Math.max(8, Math.min(above >= 8 ? above : button.bottom + 8, window.innerHeight - box.height - 8));
      setPosition({ top, left });
    };
    place();
    const dismiss = () => setPreviewOpen(false);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [previewOpen, citation.excerpt]);

  return (
    <sup className="relative">
      <button
        ref={anchor}
        type="button"
        data-testid={`citation-marker-${citation.id}`}
        data-verdict={citation.verdict}
        onClick={() => { setPreviewOpen(false); onOpen(citation); }}
        onMouseEnter={() => setPreviewOpen(true)}
        onMouseLeave={() => setPreviewOpen(false)}
        onFocus={() => setPreviewOpen(true)}
        onBlur={() => setPreviewOpen(false)}
        aria-label={`Citation ${citation.n}: ${tip}`}
        aria-describedby={previewOpen ? tooltipId : undefined}
        onKeyDown={(event) => { if (event.key === "Escape") setPreviewOpen(false); }}
        // Tailwind's Preflight reset sets `sup { line-height: 0 }` (the
        // standard typographic sub/sup reset) — this button inherits
        // that (Preflight also resets `button { line-height: inherit }`),
        // collapsing it to zero height and making it genuinely
        // unclickable in a real browser despite looking fine in a static
        // screenshot. leading-[1.4] overrides the inherited 0 directly on
        // the element, restoring real, clickable height. Caught live via
        // Playwright (`element is not visible`, computed height: 0px) —
        // not visible from reading the JSX/CSS alone.
        className="min-w-[15px] rounded-[3px] px-[3px] font-mono text-[0.74em] font-medium leading-[1.4] transition-colors focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-focus-ring"
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
      {previewOpen ? createPortal(
        <span
          ref={preview}
          id={tooltipId}
          data-testid={`citation-preview-${citation.id}`}
          role="tooltip"
          style={{ top: position?.top ?? 0, left: position?.left ?? 0, visibility: position ? "visible" : "hidden" }}
          className="pointer-events-none fixed z-50 w-[280px] max-w-[calc(100vw-16px)] animate-fade-in rounded-md border border-border bg-surface p-3 text-left normal-case leading-normal shadow-[0_8px_24px_rgba(25,23,20,0.16)]"
        >
          <span
            className="mb-1.5 block truncate font-mono text-[10px] font-medium tracking-[0.04em]"
            style={{ color: style.fg }}
          >
            {citation.documentName}
            {locationSuffix}
          </span>
          <span className="mb-2 block text-[11px] font-medium" style={{ color: style.fg }}>{isPartial ? "Partially supported" : isUnverified ? "Could not be verified" : "Verified source"}</span>
          <span className="block whitespace-pre-line break-words font-serif text-[13px] leading-relaxed text-muted">
            {truncateExcerpt(citation.excerpt)}
          </span>
        </span>, document.body
      ) : null}
    </sup>
  );
}
