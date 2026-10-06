"use client";

import * as React from "react";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ApiError, fetchPageImage } from "@/lib/api/documents";
import type { Citation } from "@/lib/types/chat";

export interface PagePreviewDialogProps {
  /** A PDF citation (location.kind === "page"); null closes the dialog. */
  citation: Citation | null;
  onClose: () => void;
}

/** The cited page of the original PDF, rendered server-side with the cited
 * passage highlighted. */
export function PagePreviewDialog({ citation, onClose }: PagePreviewDialogProps) {
  const [imageUrl, setImageUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const page = citation?.location?.kind === "page" ? citation.location.number : null;

  React.useEffect(() => {
    if (!citation || page === null) return;
    let cancelled = false;
    let url: string | null = null;
    setImageUrl(null);
    setError(null);
    fetchPageImage(citation.documentId, page, citation.bbox)
      .then((objectUrl) => {
        url = objectUrl;
        if (cancelled) URL.revokeObjectURL(objectUrl);
        else setImageUrl(objectUrl);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't load this page.");
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [citation, page]);

  return (
    <Dialog open={citation !== null && page !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[calc(100vh-48px)] w-[760px] flex-col gap-3 overflow-hidden p-5">
        <DialogTitle className="m-0 font-serif text-lg font-medium">
          {citation?.documentName} · page {page}
        </DialogTitle>
        <DialogDescription className="m-0 text-xs text-faint">
          {citation?.bbox ? "The cited passage is highlighted." : "This citation has no stored position on the page."}
        </DialogDescription>
        <div className="min-h-[240px] flex-1 overflow-auto rounded-md border border-line bg-surface">
          {imageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element -- object URL from fetch(); next/image can't optimize it
            <img
              data-testid="page-preview-image"
              src={imageUrl}
              alt={`Page ${page} of ${citation?.documentName}`}
              className="block w-full"
            />
          ) : error ? (
            <p role="alert" className="m-0 p-6 text-center text-sm text-destructive">
              {error}
            </p>
          ) : (
            <p className="m-0 p-6 text-center font-mono text-[11px] tracking-[0.06em] text-faint">LOADING PAGE…</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
