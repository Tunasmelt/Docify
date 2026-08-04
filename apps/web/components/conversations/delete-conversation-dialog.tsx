"use client";

import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export interface DeleteConversationTarget {
  id: string;
  title: string;
}

export interface DeleteConversationDialogProps {
  target: DeleteConversationTarget | null;
  error?: string | null;
  deleting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Batch 3 — same shape as components/documents/delete-confirm-dialog.tsx.
 * Copy is accurate to this feature's real backend behavior (unlike that
 * dialog's own document-delete copy, a pre-existing, separate issue not
 * touched here): messages and citations cascade-delete with the
 * conversation (SCHEMA.md's on-delete-cascade FKs) — the *documents*
 * this conversation referenced are untouched. */
export function DeleteConversationDialog({
  target,
  error = null,
  deleting = false,
  onConfirm,
  onCancel,
}: DeleteConversationDialogProps) {
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogTitle>Delete this conversation?</DialogTitle>
        <DialogDescription>
          <span className="font-medium text-ink">{target?.title}</span> and all its
          messages will be removed. This cannot be undone. The documents it referenced
          are not affected.
        </DialogDescription>
        {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
        <div className="flex justify-end gap-2.5">
          <Button type="button" variant="outline" onClick={onCancel} disabled={deleting}>
            Keep it
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={deleting}>
            {deleting ? "Deleting…" : "Delete conversation"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
