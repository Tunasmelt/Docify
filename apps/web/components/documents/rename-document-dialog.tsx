"use client";

import * as React from "react";

import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

// Matches the API's bound (routes/documents.py FILENAME_MAX_LENGTH).
const FILENAME_MAX_LENGTH = 255;

export interface RenameDocumentTarget {
  id: string;
  currentName: string;
}

export interface RenameDocumentDialogProps {
  target: RenameDocumentTarget | null;
  error?: string | null;
  saving?: boolean;
  onConfirm: (filename: string) => void;
  onCancel: () => void;
}

/** Same dialog pattern as RenameConversationDialog. Only the display name
 * changes; the stored file and its chunks are untouched. */
export function RenameDocumentDialog({ target, error = null, saving = false, onConfirm, onCancel }: RenameDocumentDialogProps) {
  const [name, setName] = React.useState("");

  React.useEffect(() => {
    if (target) setName(target.currentName);
  }, [target]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || saving) return;
    onConfirm(trimmed);
  }

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogTitle>Rename document</DialogTitle>
        <DialogDescription>This changes the name shown in your library and in citations.</DialogDescription>
        <form onSubmit={handleSubmit}>
          <Input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={FILENAME_MAX_LENGTH}
            disabled={saving}
            placeholder="Document name"
            aria-label="Document name"
          />
          {error ? <p className="m-0 mt-2 text-[13px] text-destructive">{error}</p> : null}
          <div className="mt-6 flex justify-end gap-2.5">
            <Button type="button" variant="outline" onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
