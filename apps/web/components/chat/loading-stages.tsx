"use client";

import * as React from "react";

const STAGES = ["FINDING RELEVANT PAGES…", "READING…", "CHECKING SOURCES…"];

export interface LoadingStagesProps {
  /** 0-indexed stage — the caller (chat page) owns the timer, since it
   * also owns when the real /query response actually lands. */
  stage: number;
}

export function LoadingStages({ stage }: LoadingStagesProps) {
  const label = STAGES[Math.min(stage, STAGES.length - 1)];

  return (
    <div className="max-w-[85%]">
      <p className="m-0 flex items-center gap-2 font-mono text-[11px] tracking-[0.06em] text-muted">
        <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent" />
        {label}
      </p>
      <div className="mt-3 flex flex-col gap-2.5">
        <div className="h-3 w-4/5 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
        <div className="h-3 w-3/5 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
        <div className="h-3 w-[70%] animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
      </div>
    </div>
  );
}
