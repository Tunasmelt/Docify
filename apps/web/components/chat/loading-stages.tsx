"use client";

import * as React from "react";

// FEAT-016 (2026-07-27): rebuilt from a 3-stage timer-driven fake
// (advance every LOADING_STAGE_INTERVAL_MS regardless of real backend
// progress) into a 2-state component driven by REAL SSE events. The
// old middle "READING…" stage is gone entirely — that gap is now
// filled by the real answer text streaming in token-by-token, which is
// the whole point of this feature, not a third synthetic stage to keep
// simulating.
export type StreamingStage = "retrieving" | "verifying";

const LABELS: Record<StreamingStage, string> = {
  retrieving: "FINDING RELEVANT PAGES…",
  verifying: "CHECKING SOURCES…",
};

export interface LoadingStagesProps {
  stage: StreamingStage;
}

export function LoadingStages({ stage }: LoadingStagesProps) {
  return (
    <div className="max-w-[85%]">
      <p className="m-0 flex items-center gap-2 font-mono text-[11px] tracking-[0.06em] text-muted">
        <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent" />
        {LABELS[stage]}
      </p>
      {stage === "retrieving" ? (
        // Only shown before any text exists at all. Once tokens start
        // arriving the real message bubble takes over — there is
        // nothing left to skeleton-shimmer once real content is
        // visible, so "verifying" (which always follows real,
        // already-rendered text) renders the label alone.
        <div className="mt-3 flex flex-col gap-2.5">
          <div className="h-3 w-4/5 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
          <div className="h-3 w-3/5 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
          <div className="h-3 w-[70%] animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
        </div>
      ) : null}
    </div>
  );
}
