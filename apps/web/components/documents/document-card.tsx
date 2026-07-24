"use client";

import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { DOCUMENT_STATUS_STYLES, type DocumentStatus } from "@/lib/status-styles";

export interface DocumentCardData {
  id: string;
  filename: string;
  pages: number | null;
  date: string;
  status: DocumentStatus;
}

export interface DocumentCardProps {
  doc: DocumentCardData;
  onDelete: (id: string) => void;
}

export function DocumentCard({ doc, onDelete }: DocumentCardProps) {
  const style = DOCUMENT_STATUS_STYLES[doc.status];
  const meta = `${doc.pages !== null ? `${doc.pages} PP` : "— PP"} · ${doc.date}`;

  return (
    <div className="flex items-center gap-4 border-b border-line px-[18px] py-3.5 last:border-b-0 hover:bg-panel-hover">
      <div className="flex h-9 w-7 flex-shrink-0 items-center justify-center rounded-[3px] border border-border bg-surface font-serif text-sm text-faint">
        ¶
      </div>
      <div className="min-w-0 flex-1">
        <p className="m-0 truncate font-serif text-[15px] font-medium">
          {doc.filename}
        </p>
        <p className="m-0 mt-0.5 font-mono text-[11px] tracking-[0.04em] text-faint">
          {meta}
        </p>
      </div>
      <Badge fg={style.fg} bg={style.bg}>
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background: style.fg,
            animation: style.live ? "pulseDot 1.4s ease-in-out infinite" : "none",
          }}
        />
        {style.label}
      </Badge>
      <button
        type="button"
        title="Delete"
        onClick={() => onDelete(doc.id)}
        className="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-md text-faint transition-colors hover:bg-panel-active hover:text-destructive"
      >
        <Trash2 size={15} strokeWidth={1.8} />
      </button>
    </div>
  );
}
