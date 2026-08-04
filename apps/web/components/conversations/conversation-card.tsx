"use client";

// Same row layout as components/documents/document-card.tsx (icon
// square, serif title, mono/faint meta line, hover-panel row) — kept
// as its own component for the same reason DocumentCard is: this
// project's convention is one card component per list-row shape, not
// inline JSX repeated across pages. Rename/delete (batch 3) reuse
// DocumentCard's own always-visible (not hover-only) action-button
// treatment, the closer sibling precedent for a list row.

import { Pencil, Trash2 } from "lucide-react";

const MAX_INLINE_DOCUMENT_NAMES = 2;

export interface ConversationCardData {
  id: string;
  title: string;
  /** Filenames of every document this conversation is scoped to,
   * resolved client-side from `document_ids` against the user's real
   * document list (GET /conversations doesn't return names itself —
   * see app/(app)/chat/page.tsx). Empty when the source document(s)
   * have since been deleted. */
  documentNames: string[];
  messageCount: number;
  /** Already formatted for display (see formatUpdatedAt in the page). */
  updatedAtLabel: string;
}

export interface ConversationCardProps {
  conversation: ConversationCardData;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
}

function formatDocumentNames(names: string[]): string {
  if (names.length === 0) return "No documents";
  if (names.length <= MAX_INLINE_DOCUMENT_NAMES) return names.join(", ");
  const shown = names.slice(0, MAX_INLINE_DOCUMENT_NAMES).join(", ");
  return `${shown} +${names.length - MAX_INLINE_DOCUMENT_NAMES} more`;
}

export function ConversationCard({ conversation, onRename, onDelete }: ConversationCardProps) {
  const meta = `${formatDocumentNames(conversation.documentNames)} · ${conversation.messageCount} ${
    conversation.messageCount === 1 ? "MESSAGE" : "MESSAGES"
  } · ${conversation.updatedAtLabel}`;

  return (
    // relative wrapper, not the <a> itself — the action buttons below
    // are siblings of the <a>, absolutely positioned on top of it,
    // rather than nested inside it (an interactive <button> nested
    // inside an <a> is invalid HTML and makes click targeting
    // unreliable across browsers).
    <div className="group relative border-b border-line last:border-b-0">
      <a
        href={`/chat/${conversation.id}`}
        className="flex items-center gap-4 px-[18px] py-3.5 pr-[92px] no-underline hover:bg-panel-hover"
      >
        <div className="flex h-9 w-7 flex-shrink-0 items-center justify-center rounded-[3px] border border-border bg-surface font-serif text-sm text-faint">
          ¶
        </div>
        <div className="min-w-0 flex-1">
          <p className="m-0 truncate font-serif text-[15px] font-medium text-ink">{conversation.title}</p>
          <p className="m-0 mt-0.5 truncate font-mono text-[11px] tracking-[0.04em] text-faint">{meta}</p>
        </div>
      </a>
      <div className="absolute right-[18px] top-1/2 flex -translate-y-1/2 items-center gap-1">
        <button
          type="button"
          title="Rename"
          data-testid={`rename-conversation-${conversation.id}`}
          onClick={(e) => {
            e.preventDefault();
            onRename(conversation.id);
          }}
          className="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-md text-faint transition-colors hover:bg-panel-active hover:text-ink"
        >
          <Pencil size={15} strokeWidth={1.8} />
        </button>
        <button
          type="button"
          title="Delete"
          data-testid={`delete-conversation-${conversation.id}`}
          onClick={(e) => {
            e.preventDefault();
            onDelete(conversation.id);
          }}
          className="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-md text-faint transition-colors hover:bg-panel-active hover:text-destructive"
        >
          <Trash2 size={15} strokeWidth={1.8} />
        </button>
      </div>
    </div>
  );
}
