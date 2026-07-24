"use client";

import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export interface DeleteConfirmDialogProps {
  filename: string | null;
  error?: string | null;
  deleting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeleteConfirmDialog({
  filename,
  error = null,
  deleting = false,
  onConfirm,
  onCancel,
}: DeleteConfirmDialogProps) {
  return (
    <Dialog open={filename !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogTitle>Delete this document?</DialogTitle>
        <DialogDescription>
          <span className="font-medium text-ink">{filename}</span> and every
          conversation that cites it will be removed. This cannot be undone.
        </DialogDescription>
        {error ? (
          <p className="m-0 text-[13px] text-destructive">{error}</p>
        ) : null}
        <div className="flex justify-end gap-2.5">
          <Button type="button" variant="outline" onClick={onCancel} disabled={deleting}>
            Keep it
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={deleting}>
            {deleting ? "Deleting…" : "Delete document"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
