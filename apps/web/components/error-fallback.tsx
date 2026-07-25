"use client";

import * as React from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export interface ErrorFallbackProps {
  /** The boundary's caught error — logged in full here (STANDARDS.md:
   * "technical details logged, human-readable message displayed"),
   * never rendered to the user as raw text/stack trace. */
  error: Error & { digest?: string };
  reset: () => void;
  /** Human-readable, route-specific copy — no error code, no stack
   * trace, no raw `error.message` ever shown here. */
  title: string;
  description: string;
  backHref?: string;
  backLabel?: string;
}

/** Shared by every route-level error.tsx boundary (STANDARDS.md: "Every
 * protected route has an error.tsx boundary"). No app/(app)/layout.tsx
 * exists in this project — each page builds its own Sidebar/Topbar shell
 * inline — so a route's error.tsx has no chrome to inherit and renders
 * as a minimal, on-brand full page instead of trying to reconstruct the
 * sidebar from a crashed render. */
export function ErrorFallback({
  error,
  reset,
  title,
  description,
  backHref = "/documents",
  backLabel = "Back to Documents",
}: ErrorFallbackProps) {
  React.useEffect(() => {
    // eslint-disable-next-line no-console -- the one sanctioned use:
    // STANDARDS.md requires console.error for logged technical detail,
    // never console.log.
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-6 text-center text-ink">
      <div className="font-serif text-4xl leading-none text-destructive">¶</div>
      <h1 className="m-0 mb-2 mt-4 font-serif text-2xl font-medium">{title}</h1>
      <p className="m-0 max-w-[360px] text-sm leading-relaxed text-muted">{description}</p>
      <div className="mt-6 flex items-center gap-3">
        <Button type="button" onClick={() => reset()}>
          Try again
        </Button>
        <Button asChild variant="outline">
          <Link href={backHref}>{backLabel}</Link>
        </Button>
      </div>
    </div>
  );
}
