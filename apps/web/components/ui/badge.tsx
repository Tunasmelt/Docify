import * as React from "react";

import { cn } from "@/lib/utils";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  fg: string;
  bg: string;
}

/** A pill badge with caller-supplied colors — status/verdict colors in
 * this design system are computed per-value (see STATUS_STYLES /
 * VERDICT_STYLES), not a fixed enum of Tailwind variant classes. */
function Badge({ className, fg, bg, style, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold",
        className
      )}
      style={{ color: fg, background: bg, ...style }}
      {...props}
    />
  );
}

export { Badge };
