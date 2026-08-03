"use client";

import { Badge } from "@/components/ui/badge";

const MAX_INLINE_DOCUMENT_CHIPS = 3;

export interface DocumentScopeChipsProps {
  /** Filenames of every document this conversation is scoped to,
   * resolved client-side from document_ids against the user's real
   * document list — same document_ids -> name resolution pattern
   * ConversationCard/the conversation list page already established
   * (FEAT-015 polish pass; GET /conversations and GET
   * /conversations/{id}/messages don't return names themselves), reused
   * here rather than a second name-resolution path. */
  documentNames: string[];
}

/** Batch 1, item 6 — chip row showing which document(s) a conversation
 * covers. Uses the existing neutral Badge fg/bg pair (the same
 * --muted/--muted-bg tokens as DOCUMENT_STATUS_STYLES.uploaded and
 * CITATION_VERDICT_STYLES.unverified) rather than a new color, since
 * this is informational, not a status/warning. */
export function DocumentScopeChips({ documentNames }: DocumentScopeChipsProps) {
  if (documentNames.length === 0) return null;
  const shown = documentNames.slice(0, MAX_INLINE_DOCUMENT_CHIPS);
  const overflowCount = documentNames.length - shown.length;

  return (
    <div data-testid="document-scope-chips" className="flex min-w-0 flex-wrap items-center gap-1.5">
      {shown.map((name, i) => (
        <Badge
          key={`${name}-${i}`}
          fg="var(--muted)"
          bg="var(--muted-bg)"
          title={name}
          className="min-w-0 max-w-[160px] truncate rounded-md py-1 font-mono text-[10px] font-medium tracking-[0.02em]"
        >
          {name}
        </Badge>
      ))}
      {overflowCount > 0 ? (
        <span className="flex-shrink-0 font-mono text-[10px] tracking-[0.04em] text-faint">+{overflowCount} more</span>
      ) : null}
    </div>
  );
}
