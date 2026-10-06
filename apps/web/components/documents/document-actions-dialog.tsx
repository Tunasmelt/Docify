"use client";

import * as React from "react";
import Link from "next/link";
import { ExternalLink, FileText, Loader2, MessageSquare, X } from "lucide-react";
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
    <DialogContent className={`max-h-[calc(100dvh-48px)] overflow-y-auto ${viewing ? "w-[1000px]" : "w-[480px]"}`}>
      <div className="mb-4 flex items-center justify-between">
        <span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-muted"><FileText size={15} strokeWidth={1.6} aria-hidden="true" />Library document</span>
        <Button variant="ghost" size="icon" aria-label="Close document" onClick={onClose}><X size={18} /></Button>
      </div>
      <DialogTitle className="break-words pr-2">{doc?.filename}</DialogTitle>
      <DialogDescription>View the original document or start a conversation about it.</DialogDescription>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => { setViewing(true); setAttempt((value) => value + 1); }}><FileText size={16} aria-hidden="true" />View document</Button>
        {doc?.status === "ready" ? <Button asChild><Link href={`/chat/new?docs=${doc.id}`}><MessageSquare size={16} aria-hidden="true" />Chat with document</Link></Button> : <Button disabled>Chat available when ready</Button>}
      </div>
      {viewing && <div className="mt-4">
        {error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/20 bg-destructive-bg p-4 text-sm text-destructive"><p>{error}</p><Button variant="outline" size="sm" onClick={() => setAttempt((value) => value + 1)}>Try again</Button></div> : !file ? <p role="status" className="flex min-h-32 items-center justify-center gap-2 rounded-md border border-line bg-panel text-sm text-muted"><Loader2 size={16} className="animate-spin" aria-hidden="true" />Loading document…</p> : <>
          <div className="border-t border-line pt-4"><Button asChild variant="link" size="sm" className="h-auto px-0"><a href={file.url} target="_blank" rel="noopener noreferrer">Open original in a new tab<ExternalLink size={14} aria-hidden="true" /></a></Button></div>
          {file.mime_type === "application/pdf" ? <iframe title={`Preview ${doc?.filename}`} src={file.url} className="mt-3 h-[55dvh] w-full rounded-md border border-line bg-surface" /> : <p className="mt-3 rounded-md border border-line bg-panel p-4 text-sm leading-relaxed text-muted">Use the link to open or download the original file.</p>}
        </>}
      </div>}
    </DialogContent>
  </Dialog>;
}
