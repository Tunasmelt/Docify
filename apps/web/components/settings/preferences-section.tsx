"use client";

import * as React from "react";

import { SettingsSection } from "@/components/settings/settings-section";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { usePreferences, DEFAULT_QUERY_PREFERENCES } from "@/hooks/use-preferences";

const K_MIN = 1;
const K_MAX = 50;

/** Settings batch 2 — query-time preferences. Real localStorage
 * persistence (hooks/use-preferences.ts has the full storage-decision
 * writeup) — no backend round-trip to save/load any of these three.
 * `rerank` is the one preference with a real, measured cost, so it
 * gets an explicit warning rather than reading like a free toggle
 * (see the rerank checkbox's own description below). */
export function PreferencesSection() {
  const [preferences, setPreferences] = usePreferences();
  const [kInput, setKInput] = React.useState(String(preferences.defaultK));

  // Keeps the visible text field in sync when the underlying preference
  // loads client-side after mount (hooks/use-preferences.ts's SSR-safe
  // deferred read) — without this the field would keep showing the
  // DEFAULT_QUERY_PREFERENCES value even after the real stored one loads.
  React.useEffect(() => {
    setKInput(String(preferences.defaultK));
  }, [preferences.defaultK]);

  function commitK() {
    const parsed = Number.parseInt(kInput, 10);
    if (Number.isNaN(parsed)) {
      setKInput(String(preferences.defaultK));
      return;
    }
    const clamped = Math.min(K_MAX, Math.max(K_MIN, parsed));
    setKInput(String(clamped));
    setPreferences({ defaultK: clamped });
  }

  return (
    <SettingsSection
      title="Query preferences"
      description="Defaults applied to new questions in this browser."
    >
      <div className="flex flex-col gap-1.5">
        <label htmlFor="default-k" className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
          Default sources per question
        </label>
        <Input
          id="default-k"
          type="number"
          inputMode="numeric"
          min={K_MIN}
          max={K_MAX}
          value={kInput}
          onChange={(e) => setKInput(e.target.value)}
          onBlur={commitK}
          className="max-w-[120px]"
        />
        <p className="m-0 text-[11px] text-faint">
          How many document passages to retrieve per question ({K_MIN}–{K_MAX}). Higher can surface more
          relevant context but costs more per query.
        </p>
      </div>

      <div className="border-t border-line pt-5">
        <label className="flex cursor-pointer items-start gap-2.5">
          <Checkbox
            checked={preferences.rerank}
            onCheckedChange={(checked) => setPreferences({ rerank: checked === true })}
            className="mt-0.5"
          />
          <span>
            <span className="block text-[14px] font-medium">Rerank results for relevance</span>
            <span className="mt-0.5 block max-w-[440px] text-[13px] leading-relaxed text-faint">
              Sends retrieved passages through a second, real reranking pass before answering.{" "}
              <span className="font-medium text-ink">Adds roughly 380ms to every question</span> — this
              project's own measurements found it made results no worse, but improvement over the default
              wasn't demonstrated for typical questions either. Off by default; turn on if you're seeing
              retrieval quality issues.
            </span>
          </span>
        </label>
      </div>

      <div className="border-t border-line pt-5">
        <label className="flex cursor-pointer items-start gap-2.5">
          <Checkbox
            checked={preferences.streaming}
            onCheckedChange={(checked) => setPreferences({ streaming: checked === true })}
            className="mt-0.5"
          />
          <span>
            <span className="block text-[14px] font-medium">Stream answers as they generate</span>
            <span className="mt-0.5 block max-w-[440px] text-[13px] leading-relaxed text-faint">
              Shows text as it's written, with live retrieving/verifying progress. Turn off to wait for the
              complete, verified answer in one response — {" "}
              {preferences.streaming ? "default." : "no live progress indicator, single wait instead."}
            </span>
          </span>
        </label>
      </div>

      {(preferences.defaultK !== DEFAULT_QUERY_PREFERENCES.defaultK ||
        preferences.rerank !== DEFAULT_QUERY_PREFERENCES.rerank ||
        preferences.streaming !== DEFAULT_QUERY_PREFERENCES.streaming) && (
        <button
          type="button"
          onClick={() => {
            setPreferences(DEFAULT_QUERY_PREFERENCES);
            setKInput(String(DEFAULT_QUERY_PREFERENCES.defaultK));
          }}
          className="w-fit border-none bg-transparent p-0 text-[13px] font-medium text-accent underline-offset-2 hover:underline"
        >
          Reset to defaults
        </button>
      )}
    </SettingsSection>
  );
}
