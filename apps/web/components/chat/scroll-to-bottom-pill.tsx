"use client";

export interface ScrollToBottomPillProps {
  onClick: () => void;
}

/** Batch 1, item 4 — shown when the message list auto-scroll is
 * suppressed because the user has scrolled up to reread something.
 * animate-fade-up and the shadow/border treatment reuse the same
 * tokens as source-panel.tsx's overlay entrance rather than inventing
 * a new motion/elevation language for one element. */
export function ScrollToBottomPill({ onClick }: ScrollToBottomPillProps) {
  return (
    <button
      type="button"
      data-testid="scroll-to-bottom-pill"
      onClick={onClick}
      className="absolute bottom-4 left-1/2 z-30 -translate-x-1/2 animate-fade-up whitespace-nowrap rounded-full border border-border bg-surface px-3.5 py-1.5 text-[12.5px] font-medium text-ink shadow-[0_4px_16px_rgba(25,23,20,0.14)] hover:bg-panel-hover"
    >
      New message ↓
    </button>
  );
}
