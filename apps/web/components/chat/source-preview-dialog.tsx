"use client";

import * as React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ApiError, fetchPageImage, fetchSourceContext, type SourceContext } from "@/lib/api/documents";
import type { Citation } from "@/lib/types/chat";

export interface SourcePreviewDialogProps {
  /** The citation to show in its document; null closes the dialog. */
  citation: Citation | null;
  onClose: () => void;
}

/** "Open in document" for a citation. PDFs show the cited page rendered
 * server-side with the passage highlighted; DOCX/PPTX/HTML have no page
 * image, so they show the cited slide or section as text with the cited
 * passage highlighted. */
export function SourcePreviewDialog({ citation, onClose }: SourcePreviewDialogProps) {
  const page = citation?.location?.kind === "page" ? citation.location.number : null;
  const title = citation
    ? citation.location
      ? `${citation.documentName} · ${citation.location.kind} ${citation.location.number}`
      : citation.documentName
    : "";

  return (
    <Dialog open={citation !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[calc(100vh-48px)] w-[760px] flex-col gap-3 overflow-hidden p-5">
        <DialogTitle className="m-0 font-serif text-lg font-medium">{title}</DialogTitle>
        {citation && page !== null ? (
          <PageImage citation={citation} page={page} />
        ) : citation ? (
          <ContextView citation={citation} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function PageImage({ citation, page }: { citation: Citation; page: number }) {
  const [imageUrl, setImageUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
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
    <>
      <DialogDescription className="m-0 text-xs text-faint">
        {citation.bbox ? "The cited passage is highlighted." : "This citation has no stored position on the page."}
      </DialogDescription>
      <div className="min-h-[240px] flex-1 overflow-auto rounded-md border border-line bg-surface">
        {imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- object URL from fetch(); next/image can't optimize it
          <img
            data-testid="page-preview-image"
            src={imageUrl}
            alt={`Page ${page} of ${citation.documentName}`}
            className="block w-full"
          />
        ) : error ? (
          <Status error>{error}</Status>
        ) : (
          <Status>LOADING PAGE…</Status>
        )}
      </div>
    </>
  );
}

const markdownComponents: Components = {
  p: ({ children }) => <p className="m-0 whitespace-pre-line">{children}</p>,
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[0.9em]">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-line px-2 py-1 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-line px-2 py-1">{children}</td>,
};

function ContextView({ citation }: { citation: Citation }) {
  const [context, setContext] = React.useState<SourceContext | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const citedRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    let cancelled = false;
    setContext(null);
    setError(null);
    fetchSourceContext(citation.documentId, citation.chunkId)
      .then((result) => {
        if (!cancelled) setContext(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't load this part of the document.");
      });
    return () => {
      cancelled = true;
    };
  }, [citation]);

  React.useEffect(() => {
    citedRef.current?.scrollIntoView({ block: "center" });
  }, [context]);

  return (
    <>
      <DialogDescription className="m-0 text-xs text-faint">
        {context?.kind === "slide"
          ? "Everything on this slide. The cited passage is highlighted."
          : "The surrounding section of the document. The cited passage is highlighted."}
      </DialogDescription>
      <div className="min-h-[240px] flex-1 overflow-auto rounded-md border border-line bg-surface p-5">
        {context ? (
          <div className="flex flex-col gap-4">
            {context.label && context.kind === "section" ? (
              <h3 className="m-0 font-serif text-base font-medium">{context.label}</h3>
            ) : null}
            {context.blocks.map((block) => (
              <div
                key={block.chunk_id}
                ref={block.cited ? citedRef : undefined}
                data-testid="source-block"
                data-cited={block.cited ? "true" : "false"}
                className={
                  block.cited
                    ? "rounded-md border-l-[3px] border-amber bg-amber-bg px-3 py-2 text-[14px] leading-relaxed text-ink"
                    : "px-3 text-[14px] leading-relaxed text-muted"
                }
              >
                {block.figure_url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- short-lived signed Storage URL
                  <img
                    src={block.figure_url}
                    alt={block.content || "Figure"}
                    className="mb-1.5 max-h-[320px] w-full rounded-md border border-line bg-surface object-contain"
                  />
                ) : null}
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {block.content}
                </ReactMarkdown>
              </div>
            ))}
          </div>
        ) : error ? (
          <Status error>{error}</Status>
        ) : (
          <Status>LOADING…</Status>
        )}
      </div>
    </>
  );
}

function Status({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return error ? (
    <p role="alert" className="m-0 p-6 text-center text-sm text-destructive">
      {children}
    </p>
  ) : (
    <p className="m-0 p-6 text-center font-mono text-[11px] tracking-[0.06em] text-faint">{children}</p>
  );
}
