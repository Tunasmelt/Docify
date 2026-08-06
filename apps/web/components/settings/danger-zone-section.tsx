"use client";

import * as React from "react";

import { SettingsSection } from "@/components/settings/settings-section";
import { DeleteAccountDialog } from "@/components/settings/delete-account-dialog";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/browser";
import { ProfileError, deleteAccount } from "@/lib/supabase/profile";

export interface DangerZoneSectionProps {
  accountEmail: string;
}

/** Settings batch 3, part 2 — permanent account deletion. Kept as its
 * own section, deliberately separate from SecuritySection: everything
 * else in Settings is either reversible or, at worst, a metadata change
 * — this is the one irreversible action in the entire app, and treating
 * it as its own clearly-labeled "Danger zone" matches how seriously
 * this project already treats destructive actions elsewhere
 * (DeleteConfirmDialog's own red-flagged copy for a single document).
 *
 * No loading-then-redirect race: on success, this hard-navigates to
 * /login via window.location.href (not router.push) — same reasoning
 * as lib/api/client.ts's forceReauth(): guarantees a full reload with
 * zero stale client-side state (React Query caches, in-memory
 * preferences, anything) surviving into a session for an account that
 * no longer exists.
 *
 * REAL BUG found live by e2e/account-deletion.e2e.ts, not assumed away:
 * an earlier version of this skipped signOut() entirely, reasoning the
 * account (and therefore its session) was already gone server-side by
 * the time deleteAccount() resolves — true for the BACKEND, but the
 * BROWSER's own Supabase session cookie is untouched by that, and
 * middleware.ts makes its /login-vs-/documents redirect decision from
 * that cookie alone (a cryptographically-valid JWT, same "stale but
 * still valid" fact this feature's own API_CONTRACT.md entry documents
 * for the backend side). Without clearing it first, navigating to
 * /login got immediately bounced back to /documents by the middleware,
 * which still believed the user was authenticated — confirmed live,
 * not theoretical. `scope: 'local'` clears only this browser's own
 * session/storage with NO server round-trip (unlike the default scope,
 * which would call GoTrue's /logout for a user that no longer exists,
 * for no benefit — same reasoning the earlier version had, just applied
 * to the wrong call). */
export function DangerZoneSection({ accountEmail }: DangerZoneSectionProps) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const supabase = React.useMemo(() => createClient(), []);

  async function handleConfirm(password: string) {
    setError(null);
    setDeleting(true);
    try {
      await deleteAccount(accountEmail, password);
      await supabase.auth.signOut({ scope: "local" });
      window.location.href = "/login?error=" + encodeURIComponent("Your account has been permanently deleted.");
    } catch (err) {
      setError(err instanceof ProfileError ? err.message : "Couldn't delete your account. Try again.");
      setDeleting(false);
    }
  }

  return (
    <SettingsSection title="Danger zone" description="Permanently delete your account and everything on it.">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="m-0 text-[14px] font-medium">Delete account</p>
          <p className="m-0 mt-0.5 max-w-[420px] text-[13px] leading-relaxed text-faint">
            Deletes every document, conversation, and citation permanently. This cannot be
            undone — export your data first if you might want it later.
          </p>
        </div>
        <Button type="button" variant="destructive" size="sm" onClick={() => setDialogOpen(true)}>
          Delete account
        </Button>
      </div>

      <DeleteAccountDialog
        open={dialogOpen}
        accountEmail={accountEmail}
        onConfirm={handleConfirm}
        onCancel={() => {
          setDialogOpen(false);
          setError(null);
        }}
        deleting={deleting}
        error={error}
      />
    </SettingsSection>
  );
}
