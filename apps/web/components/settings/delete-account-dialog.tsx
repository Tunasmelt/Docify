"use client";

import * as React from "react";

import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface DeleteAccountDialogProps {
  open: boolean;
  accountEmail: string;
  onConfirm: (password: string) => void;
  onCancel: () => void;
  deleting?: boolean;
  error?: string | null;
}

/** Settings batch 3, part 2 — the confirmation step for permanent
 * account deletion (item 8 of the task this was built from: "genuinely
 * hard to trigger accidentally"). Two independent pieces of friction,
 * both required:
 *
 * 1. Current password — the real security boundary (verified server-
 *    side via a real signInWithPassword() call, lib/supabase/profile.ts's
 *    deleteAccount) — same bar this project already requires for
 *    email-change, reused here for consistency, not a new mechanism.
 * 2. Typing the account's own real email — pure UI friction, NOT a
 *    security check (it proves nothing a password doesn't already
 *    prove) — its only job is making a genuine misclick/mis-tap
 *    structurally impossible: a user has to consciously read and
 *    retype their own email, not just double-click through a generic
 *    "Are you sure?" dialog the way DeleteConfirmDialog's single-click
 *    confirm (components/documents/delete-confirm-dialog.tsx) is
 *    appropriate for a recoverable-by-reupload document delete but
 *    isn't enough friction for something this irreversible.
 *
 * Both fields reset whenever the dialog closes (cancel, or a failed
 * attempt where the user backs out) — a stale password sitting in a
 * closed dialog's state, silently reused on a later reopen, is exactly
 * the kind of thing that shouldn't survive a cancel for an action this
 * serious. */
export function DeleteAccountDialog({
  open,
  accountEmail,
  onConfirm,
  onCancel,
  deleting = false,
  error = null,
}: DeleteAccountDialogProps) {
  const [password, setPassword] = React.useState("");
  const [confirmEmail, setConfirmEmail] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setPassword("");
      setConfirmEmail("");
    }
  }, [open]);

  const emailMatches = confirmEmail.trim() === accountEmail;
  const canConfirm = emailMatches && password.length > 0 && !deleting;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent>
        <DialogTitle>Permanently delete your account?</DialogTitle>
        <DialogDescription>
          Every document, conversation, and citation on this account will be deleted
          immediately and permanently. This cannot be undone — there is no recovery.
        </DialogDescription>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="delete-confirm-email" className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
            Type <span className="font-mono normal-case text-ink">{accountEmail}</span> to confirm
          </label>
          <Input
            id="delete-confirm-email"
            type="text"
            value={confirmEmail}
            onChange={(e) => setConfirmEmail(e.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="delete-confirm-password" className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
            Current password
          </label>
          <Input
            id="delete-confirm-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>

        {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}

        <div className="flex justify-end gap-2.5">
          <Button type="button" variant="outline" onClick={onCancel} disabled={deleting}>
            Keep my account
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!canConfirm}
            onClick={() => onConfirm(password)}
          >
            {deleting ? "Deleting…" : "Permanently delete account"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
