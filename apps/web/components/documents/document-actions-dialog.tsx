"use client";

import * as React from "react";
import Link from "next/link";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ApiError, getDocumentFile, type ApiDocument } from "@/lib/api/documents";

export function DocumentActionsDialog({ doc, onClose }: { doc: ApiDocument | null; onClose: () => void }) {
  const [viewing, setViewing] = React.useState(false);
  const [file, setFile] = React.useState<{ url: string; mime_type: string } | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);
  React.useEffect(() => {
    setViewing(false); setFile(null); setError(null);
  }, [doc?.id]);
  React.useEffect(() => {
    if (!doc || !viewing) return;
    let cancelled = false;
    setFile(null); setError(null);
    getDocumentFile(doc.id).then((result) => {
      if (!cancelled) setFile(result);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't open this document.");
    });
    return () => { cancelled = true; };
  }, [doc?.id, doc, viewing, attempt]);
  return <Dialog open={!!doc} onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className={viewing ? "w-[1000px]" : undefined}>
      <DialogTitle className="break-words">{doc?.filename}</DialogTitle>
      <DialogDescription>View the original document or start a conversation about it.</DialogDescription>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => { setViewing(true); setAttempt((value) => value + 1); }}>View document</Button>
        {doc?.status === "ready" ? <Button asChild><Link href={`/chat/new?docs=${doc.id}`}>Chat with document</Link></Button> : <Button disabled>Chat available when ready</Button>}
        <Button variant="ghost" onClick={onClose}>Close</Button>
      </div>
      {viewing && <div className="mt-4">
        {error ? <p role="alert">{error} <button className="underline" onClick={() => setAttempt((value) => value + 1)}>Try again</button></p> : !file ? <p role="status">Loading document…</p> : <>
          <a href={file.url} target="_blank" rel="noopener noreferrer" className="text-sm text-accent underline">Open original in a new tab</a>
          {file.mime_type === "application/pdf" ? <iframe title={`Preview ${doc?.filename}`} src={file.url} className="mt-3 h-[60vh] w-full rounded border border-line" /> : <p className="mt-3 text-sm text-muted">Use the link to open or download the original file.</p>}
        </>}
      </div>}
    </DialogContent>
  </Dialog>;
}
