"use client";

import * as React from "react";

import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

// Matches the backend's own bound (routes/conversations.py's
// TITLE_MAX_LENGTH, itself matching create_query_turn's auto-generated-
// title truncation) — enforced here too so the field's own maxLength
// gives immediate feedback instead of waiting on a round-trip 422.
const TITLE_MAX_LENGTH = 200;

export interface RenameConversationTarget {
  id: string;
  currentTitle: string;
}

export interface RenameConversationDialogProps {
  target: RenameConversationTarget | null;
  error?: string | null;
  saving?: boolean;
  onConfirm: (title: string) => void;
  onCancel: () => void;
}

/** Batch 3 — same Dialog-based confirm pattern as
 * components/documents/delete-confirm-dialog.tsx, reused rather than an
 * inline-edit-in-place treatment, so rename and delete share one visual
 * language across this app rather than each inventing its own. */
export function RenameConversationDialog({
  target,
  error = null,
  saving = false,
  onConfirm,
  onCancel,
}: RenameConversationDialogProps) {
  const [title, setTitle] = React.useState("");

  // Re-seed the draft from the target's current title every time a new
  // target is opened — not on every render, so the user's in-progress
  // edit isn't clobbered by an unrelated re-render while the dialog is
  // already open.
  React.useEffect(() => {
    if (target) setTitle(target.currentTitle);
  }, [target]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed || saving) return;
    onConfirm(trimmed);
  }

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogTitle>Rename conversation</DialogTitle>
        <DialogDescription>Give this conversation a new title.</DialogDescription>
        <form onSubmit={handleSubmit}>
          <Input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={TITLE_MAX_LENGTH}
            disabled={saving}
            placeholder="Conversation title"
          />
          {error ? <p className="m-0 mt-2 text-[13px] text-destructive">{error}</p> : null}
          <div className="mt-6 flex justify-end gap-2.5">
            <Button type="button" variant="outline" onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" disabled={!title.trim() || saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
