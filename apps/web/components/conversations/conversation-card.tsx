"use client";

// Same row layout as components/documents/document-card.tsx (icon
// square, serif title, mono/faint meta line, hover-panel row) — kept
// as its own component for the same reason DocumentCard is: this
// project's convention is one card component per list-row shape, not
// inline JSX repeated across pages.

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
}

function formatDocumentNames(names: string[]): string {
  if (names.length === 0) return "No documents";
  if (names.length <= MAX_INLINE_DOCUMENT_NAMES) return names.join(", ");
  const shown = names.slice(0, MAX_INLINE_DOCUMENT_NAMES).join(", ");
  return `${shown} +${names.length - MAX_INLINE_DOCUMENT_NAMES} more`;
}

export function ConversationCard({ conversation }: ConversationCardProps) {
  const meta = `${formatDocumentNames(conversation.documentNames)} · ${conversation.messageCount} ${
    conversation.messageCount === 1 ? "MESSAGE" : "MESSAGES"
  } · ${conversation.updatedAtLabel}`;

  return (
    <a
      href={`/chat/${conversation.id}`}
      className="flex items-center gap-4 border-b border-line px-[18px] py-3.5 no-underline last:border-b-0 hover:bg-panel-hover"
    >
      <div className="flex h-9 w-7 flex-shrink-0 items-center justify-center rounded-[3px] border border-border bg-surface font-serif text-sm text-faint">
        ¶
      </div>
      <div className="min-w-0 flex-1">
        <p className="m-0 truncate font-serif text-[15px] font-medium text-ink">{conversation.title}</p>
        <p className="m-0 mt-0.5 truncate font-mono text-[11px] tracking-[0.04em] text-faint">{meta}</p>
      </div>
    </a>
  );
}
