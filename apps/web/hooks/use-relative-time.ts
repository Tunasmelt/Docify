"use client";

import * as React from "react";

// Intl.RelativeTimeFormat — native, no new date-formatting dependency
// (checked: the codebase's only prior date formatting, the conversation
// list's formatUpdatedAt, already uses the native Intl.DateTimeFormat
// for absolute dates the same way; this follows the same precedent for
// relative ones instead of pulling in date-fns/dayjs for one hook).
const RTF = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "narrow" });

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];

function formatRelative(iso: string): string {
  const diffSeconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSeconds < 45) return "just now";
  for (const [unit, secondsInUnit] of UNITS) {
    if (diffSeconds >= secondsInUnit) {
      return RTF.format(-Math.round(diffSeconds / secondsInUnit), unit);
    }
  }
  return RTF.format(-Math.round(diffSeconds / 60), "minute");
}

/** Relative time string ("2m ago") for a message timestamp — re-renders
 * itself every 30s so a conversation left open stays fresh without the
 * caller having to manage a timer. Real ISO timestamp is always still
 * available separately for a `title` attribute (batch 1, item 2: hover
 * shows the real time). */
export function useRelativeTime(iso: string): string {
  const [, forceTick] = React.useReducer((n: number) => n + 1, 0);
  React.useEffect(() => {
    const id = setInterval(forceTick, 30000);
    return () => clearInterval(id);
  }, []);
  return formatRelative(iso);
}
