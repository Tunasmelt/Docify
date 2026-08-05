"use client";

import * as React from "react";

import { SettingsSection } from "@/components/settings/settings-section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { ProfileError, requestEmailChange } from "@/lib/supabase/profile";

export interface EmailSectionProps {
  currentEmail: string;
}

/** Part 1, item 3 — investigated first (see lib/supabase/profile.ts's
 * requestEmailChange docstring): reuses the existing /auth/callback
 * route unchanged, no new confirmation handling needed.
 *
 * Real, empirically-confirmed behavior (not assumed from the config
 * comment's wording — that reading turned out to be wrong): this
 * project's Supabase config has `double_confirm_changes = true`, which
 * sends a confirmation link to BOTH the current and new address, but
 * confirming via EITHER ONE ALONE completes the change immediately —
 * verified directly against the real local GoTrue instance (clicking
 * only the old-address link fully swaps the email; clicking only the
 * new-address link, independently tested, does too). It is not an
 * "both required" flow. The UI below reflects that: two emails go out,
 * one click from either finishes it, and using the other link
 * afterward correctly reports it as already used/expired rather than
 * asking for a second confirmation that was never actually required.
 *
 * 2026-08-05 security follow-up: requires the CURRENT password before
 * a change can even be started (lib/supabase/profile.ts's
 * requestEmailChange docstring has the full reasoning — this raises
 * the bar on who can START a change; it cannot, by itself, close the
 * gap in how the change gets CONFIRMED, which remains real and is
 * documented in .agent/GAPS.md). */
export function EmailSection({ currentEmail }: EmailSectionProps) {
  const [newEmail, setNewEmail] = React.useState("");
  const [currentPassword, setCurrentPassword] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // Once requested, show the "check both inboxes" state rather than
  // resetting to a blank form — the request already went out for real;
  // letting the user immediately retype implies it didn't.
  const [pendingEmail, setPendingEmail] = React.useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const trimmed = newEmail.trim();
    if (!trimmed || trimmed === currentEmail) return;
    if (!currentPassword) {
      setError("Enter your current password to confirm this change.");
      return;
    }

    setSubmitting(true);
    try {
      await requestEmailChange(
        currentEmail,
        currentPassword,
        trimmed,
        `${window.location.origin}/auth/callback?next=/settings`
      );
      setPendingEmail(trimmed);
      setCurrentPassword("");
    } catch (err) {
      setError(err instanceof ProfileError ? err.message : "Couldn't start the email change. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SettingsSection title="Email address" description="Used to sign in and receive account notifications.">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">Current email</span>
        <p className="m-0 text-[15px]">{currentEmail}</p>
      </div>

      {pendingEmail ? (
        <div
          data-testid="email-change-pending"
          className="rounded-md border border-amber bg-amber-bg px-3.5 py-3 text-[13px] leading-relaxed text-ink"
        >
          <p className="m-0 font-medium">Check your inbox to confirm</p>
          <p className="m-0 mt-1">
            We've sent confirmation links to <span className="font-medium">{currentEmail}</span> (your current
            address) and <span className="font-medium">{pendingEmail}</span> (the new one). Click the link in
            either email to complete the change.
          </p>
          <button
            type="button"
            onClick={() => {
              setPendingEmail(null);
              setNewEmail("");
            }}
            className="mt-2 border-none bg-transparent p-0 text-[13px] font-medium text-accent underline-offset-2 hover:underline"
          >
            Start over
          </button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="new-email" className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
              New email address
            </label>
            <Input
              id="new-email"
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="you@example.com"
              className="max-w-[320px]"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="current-password-for-email-change" className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
              Current password
            </label>
            <Input
              id="current-password-for-email-change"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder="Confirm it's you"
              autoComplete="current-password"
              className="max-w-[320px]"
            />
          </div>
          <div>
            <Button
              type="submit"
              size="sm"
              disabled={!newEmail.trim() || newEmail.trim() === currentEmail || !currentPassword || submitting}
            >
              {submitting ? "Sending…" : "Change email"}
            </Button>
          </div>
          {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
        </form>
      )}
    </SettingsSection>
  );
}
