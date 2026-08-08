"use client";

import * as React from "react";

// Settings batch 2 — investigated first (task's own item 1): none of
// these three preferences ever need server-side lookup. `k`/`rerank`
// are already pure per-request parameters QueryRequest accepts
// (apps/api/models/query.py) — the backend never reads a stored
// default on its own, it only ever uses what THIS request sent.
// `streaming` is purely "which frontend function to call"
// (askQuestion vs askQuestionStream, lib/api/query.ts) and never
// reaches the backend at all. A user_preferences table or
// user_metadata round-trip would solve a problem that doesn't exist
// for any of the three — plain localStorage is the simplest storage
// that actually works, and it's already this app's real, working
// mechanism for exactly this class of setting (next-themes persists
// theme the identical way, confirmed via components/theme-provider.tsx
// + the suppressHydrationWarning/attribute wiring already present in
// app/layout.tsx before this batch touched anything).
//
// No project rule forbids real localStorage (checked STANDARDS.md and
// every other .agent/*.md doc — no mention at all); the only real
// constraint is SSR-safety (no `window` on the server), handled below
// the same deferred-to-mount way next-themes' own `mounted` guard
// (components/theme-toggle.tsx) already established in this codebase.

const STORAGE_KEY = "docify:query-preferences";

export interface QueryPreferences {
  /** Default retrieval k for a new question — QueryRequest.k,
   * apps/api/models/query.py (ge=1, le=50). */
  defaultK: number;
  /** Default QueryRequest.rerank — real, measured ~380ms added latency
   * per query when on (.agent/MEMORY.md, 2026-07-27); defaults false,
   * matching the retrieval layer's own opt-in default. */
  rerank: boolean;
  /** Whether new questions stream (askQuestionStream) or wait for the
   * full answer (askQuestion) — pure frontend routing, never sent to
   * the backend. Defaults true, matching current/prior behavior (the
   * chat page always streamed before this preference existed). */
  streaming: boolean;
}

export const DEFAULT_QUERY_PREFERENCES: QueryPreferences = {
  defaultK: 8,
  rerank: false,
  streaming: true,
};

function loadFromStorage(): QueryPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_QUERY_PREFERENCES;
    const parsed = JSON.parse(raw);
    return {
      defaultK: typeof parsed.defaultK === "number" ? parsed.defaultK : DEFAULT_QUERY_PREFERENCES.defaultK,
      rerank: typeof parsed.rerank === "boolean" ? parsed.rerank : DEFAULT_QUERY_PREFERENCES.rerank,
      streaming: typeof parsed.streaming === "boolean" ? parsed.streaming : DEFAULT_QUERY_PREFERENCES.streaming,
    };
  } catch {
    // Malformed/corrupted localStorage value (hand-edited, a future
    // incompatible shape, etc.) — fall back to real defaults rather
    // than crash the whole app over a stored preference.
    return DEFAULT_QUERY_PREFERENCES;
  }
}

/** Query-time preferences (default k, rerank, streaming) — real
 * localStorage, SSR-safe: starts at the same DEFAULT_QUERY_PREFERENCES
 * value on both server and client (no hydration mismatch), then reads
 * the real stored value once mounted client-side, same deferred-to-
 * mount pattern next-themes' own ThemeToggle already uses in this
 * codebase. One extra render on mount is a non-issue here (unlike
 * theme, nothing visually flashes — these only take effect the next
 * time a question is actually sent). */
export function usePreferences(): [QueryPreferences, (next: Partial<QueryPreferences>) => void] {
  const [preferences, setPreferencesState] = React.useState<QueryPreferences>(DEFAULT_QUERY_PREFERENCES);

  React.useEffect(() => {
    setPreferencesState(loadFromStorage());
  }, []);

  const setPreferences = React.useCallback((next: Partial<QueryPreferences>) => {
    setPreferencesState((prev) => {
      const merged = { ...prev, ...next };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
      } catch {
        // Storage unavailable (private browsing, quota) — the in-memory
        // state still updates for this session, it just won't persist
        // across reload. Not worth surfacing as an error for a
        // preference toggle.
      }
      return merged;
    });
  }, []);

  return [preferences, setPreferences];
}
