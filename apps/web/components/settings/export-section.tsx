"use client";

import * as React from "react";

import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { downloadConversationExport, type ExportFormat } from "@/lib/api/export";

/** Settings batch 3, part 1 — export every conversation, message, and
 * citation this account owns (GET /export/conversations,
 * routes/export.py). Deliberately does NOT export source documents
 * themselves — stated explicitly in the copy below, not left for the
 * user to guess, since batch 3's second half makes account deletion
 * possible and a user relying on this export before deleting needs to
 * know exactly what it does and doesn't cover.
 *
 * No progress/job-status UI: the backend responds synchronously (this
 * project's real scale doesn't justify an async job + polling pattern
 * — routes/export.py's own module comment has the full reasoning), so
 * a simple pending state on the button while the one request is in
 * flight is the whole UX; no separate "check back later" flow needed. */
export function ExportSection() {
  const [format, setFormat] = React.useState<ExportFormat>("json");
  const [exporting, setExporting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function handleExport() {
    setError(null);
    setExporting(true);
    try {
      await downloadConversationExport(format);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't export your data. Try again.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <SettingsSection
      title="Export your data"
      description="Download every conversation, message, and citation on this account. Source documents aren't included — you already have those."
    >
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">Format</span>
        <div className="flex gap-2" role="radiogroup" aria-label="Export format">
          {(
            [
              { value: "json" as const, label: "JSON", hint: "Complete, machine-readable" },
              { value: "markdown" as const, label: "Markdown", hint: "Human-readable" },
            ]
          ).map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={format === option.value}
              onClick={() => setFormat(option.value)}
              className={`flex-1 rounded-md border px-3.5 py-2.5 text-left transition-colors ${
                format === option.value
                  ? "border-accent bg-accent/10"
                  : "border-line bg-transparent hover:bg-panel-hover"
              }`}
            >
              <span className="block text-[13px] font-medium">{option.label}</span>
              <span className="block text-[11px] text-faint">{option.hint}</span>
            </button>
          ))}
        </div>
      </div>

      <div>
        <Button type="button" size="sm" variant="outline" disabled={exporting} onClick={handleExport}>
          {exporting ? "Preparing export…" : "Export data"}
        </Button>
      </div>
      {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
    </SettingsSection>
  );
}
