"use client";

import { useTheme } from "next-themes";
import * as React from "react";

import { SettingsSection } from "@/components/settings/settings-section";
import { ThemeToggle } from "@/components/theme-toggle";

/** Settings batch 2, item 2 — theme. Persistence and no-flash-of-
 * wrong-theme were BOTH already handled before this batch touched
 * anything: next-themes itself persists to localStorage and injects a
 * blocking pre-hydration script that sets `data-theme` on `<html>`
 * before first paint — confirmed via app/layout.tsx already having
 * `suppressHydrationWarning` + `attribute="data-theme"` wired
 * correctly. This section just makes the existing ThemeToggle (already
 * used in every page's Topbar) discoverable inside Settings too, rather
 * than only living in a corner button — same toggle, same next-themes
 * state, not a second implementation. */
export function AppearanceSection() {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  return (
    <SettingsSection title="Appearance" description="Applies immediately and persists across visits.">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="m-0 text-[14px] font-medium">Theme</p>
          <p className="m-0 mt-0.5 text-[13px] text-faint">
            Currently {mounted ? (resolvedTheme === "dark" ? "dark" : "light") : "light"}.
          </p>
        </div>
        <ThemeToggle />
      </div>
    </SettingsSection>
  );
}
