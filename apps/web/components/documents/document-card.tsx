"use client";

import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
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
  /** Selection for scoping a new conversation — only offered for a
   * 'ready' document (a question can't run against one that isn't
   * embedded yet). Omitted entirely (no checkbox rendered) when the
   * caller doesn't pass onToggleSelect, so pages that don't need
   * selection (there are none currently, but this keeps the prop
   * additive rather than a breaking change) don't have to pass anything. */
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}

export function DocumentCard({ doc, onDelete, selected, onToggleSelect }: DocumentCardProps) {
  const style = DOCUMENT_STATUS_STYLES[doc.status];
  const meta = `${doc.pages !== null ? `${doc.pages} PP` : "— PP"} · ${doc.date}`;
  const selectable = doc.status === "ready" && !!onToggleSelect;

  return (
    <div className="flex items-center gap-4 border-b border-line px-[18px] py-3.5 last:border-b-0 hover:bg-panel-hover">
      {onToggleSelect ? (
        selectable ? (
          <Checkbox
            title="Select for a conversation"
            checked={selected}
            onCheckedChange={() => onToggleSelect(doc.id)}
            className="flex-shrink-0"
          />
        ) : (
          <div className="h-[18px] w-[18px] flex-shrink-0" />
        )
      ) : null}
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
