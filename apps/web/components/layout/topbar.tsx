import * as React from "react";
import { Menu } from "lucide-react";

import { cn } from "@/lib/utils";

export interface TopbarProps {
  left: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
}

/** Shared header chrome — height, border, padding, and the left/right
 * split. Each page composes its own exact content into the slots
 * (workspace badge vs. conversation title, avatar vs. no avatar, etc.)
 * since the three reference screens don't share identical header
 * content, only the same outer shape. */
export function Topbar({ left, right, className }: TopbarProps) {
  return (
    <header
      className={cn(
        "flex h-14 flex-shrink-0 items-center justify-between border-b border-line bg-bg px-4 md:px-6",
        className
      )}
    >
      <div className="flex min-w-0 items-baseline gap-2.5">{left}</div>
      {right ? (
        <div className="flex flex-shrink-0 items-center gap-3.5">{right}</div>
      ) : null}
    </header>
  );
}

export function WorkspaceBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="hidden flex-shrink-0 rounded border border-line px-1.5 py-0.5 font-mono text-[10px] tracking-[0.08em] text-faint sm:inline-block">
      {children}
    </span>
  );
}

/** Opens the mobile sidebar drawer — only rendered below `md`, matching
 * the reference's mobile shell. */
export function MobileMenuButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="-ml-2 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md text-ink md:hidden"
    >
      <Menu size={20} strokeWidth={2} />
    </button>
  );
}
