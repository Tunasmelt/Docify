"use client";

import { X } from "lucide-react";

import { CITATION_VERDICT_STYLES } from "@/lib/status-styles";
import type { Citation } from "@/lib/types/chat";

export interface SourcePanelProps {
  citation: Citation | null;
  onClose: () => void;
  onOpenInDocument: (citation: Citation) => void;
}

export function SourcePanel({ citation, onClose, onOpenInDocument }: SourcePanelProps) {
  if (!citation) return null;

  const style = CITATION_VERDICT_STYLES[citation.verdict];
  const verdictText =
    citation.verdict === "partial"
      ? "Partially supported — this page backs part of the claim. Worth a direct look."
      : `Verified — this passage supports the claim on page ${citation.page}.`;

  return (
    <aside
      data-testid="source-panel"
      className="absolute inset-y-0 right-0 z-40 w-full animate-slide-in overflow-y-auto border-l border-line bg-bg shadow-[-12px_0_40px_rgba(25,23,20,0.10)] sm:w-[400px]"
    >
      <div className="flex items-center justify-between px-5 pb-3 pt-[18px]">
        <span className="font-mono text-[11px] tracking-[0.08em] text-faint">
          SOURCE ·{" "}
          <span style={{ color: style.fg }}>{citation.n}</span>
        </span>
        <button
          type="button"
          title="Close"
          onClick={onClose}
          className="flex h-[30px] w-[30px] items-center justify-center rounded-md text-faint hover:bg-panel hover:text-ink"
        >
          <X size={16} strokeWidth={2} />
        </button>
      </div>
      <div className="px-5 pb-8">
        <h3 className="m-0 font-serif text-lg font-medium leading-snug">
          {citation.documentName}
        </h3>
        <p className="m-0 mt-0.5 font-mono text-[11px] tracking-[0.04em] text-faint">
          PAGE {citation.page}
        </p>
        <p
          className="m-0 mt-4 rounded-md px-3 py-2 text-[13px] font-medium leading-relaxed"
          style={{ color: style.fg, background: style.bg }}
        >
          {verdictText}
        </p>
        {citation.isFigure ? (
          <figure className="m-0 mt-5">
            {citation.figureUrl ? (
              // eslint-disable-next-line @next/next/no-img-element -- a
              // signed Storage URL expires (600s, FEAT-026) and is
              // re-fetched fresh on every render; next/image's
              // remote-pattern allowlist + long-lived cache assumptions
              // don't fit a URL that's deliberately short-lived and
              // unique per request.
              <img
                data-testid="figure-image"
                src={citation.figureUrl}
                alt={citation.figureCaption ?? `Figure from ${citation.documentName}, page ${citation.page}`}
                className="max-h-[240px] w-full rounded-md border border-line bg-surface object-contain"
              />
            ) : (
              <div className="flex h-[180px] items-center justify-center rounded-md border border-line bg-surface font-mono text-[11px] tracking-[0.06em] text-faint">
                Figure image unavailable
              </div>
            )}
            {citation.figureCaption ? (
              <figcaption className="mt-1.5 font-mono text-[11px] text-faint">
                {citation.figureCaption}
              </figcaption>
            ) : null}
          </figure>
        ) : null}
        <blockquote className="m-0 mt-5 border-l-2 border-border pl-4 font-serif text-[15px] leading-[1.7] text-muted">
          {citation.excerpt}
        </blockquote>
        <button
          type="button"
          onClick={() => onOpenInDocument(citation)}
          className="mt-6 w-full rounded-md border border-border py-2.5 text-sm font-medium text-muted hover:bg-panel hover:text-ink"
        >
          Open page {citation.page} in document
        </button>
      </div>
    </aside>
  );
}
