"use client";

import * as React from "react";
import { ArrowUp, Square } from "lucide-react";

export interface QuestionInputProps {
  onSend: (question: string) => void;
  /** True while an answer is actively streaming (batch 2) — swaps the
   * send button for a Stop button and disables the textarea (can't
   * start a second question while one is in flight), independent of
   * `disabled` below. */
  isStreaming?: boolean;
  onStop?: () => void;
  /** Disables the whole input for reasons unrelated to streaming
   * (loading history, conversation not found). */
  disabled?: boolean;
}

export function QuestionInput({ onSend, isStreaming, onStop, disabled }: QuestionInputProps) {
  const [draft, setDraft] = React.useState("");

  function send() {
    const q = draft.trim();
    if (!q || disabled || isStreaming) return;
    onSend(q);
    setDraft("");
  }

  return (
    <div className="flex-shrink-0 border-t border-line px-6 pb-4 pt-3">
      <div className="mx-auto max-w-[720px]">
        <div className="flex items-end gap-2 rounded-xl border border-border bg-surface p-2 transition-[box-shadow,border-color] focus-within:border-accent focus-within:ring-[3px] focus-within:ring-focus-ring">
          <textarea
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={disabled || isStreaming}
            onKeyDown={(e) => {
              // Plain Enter sends; Shift+Enter inserts a newline.
              // ⌘/Ctrl+Enter (batch 1, item 5) also sends — scoped here
              // rather than as a global shortcut since it only makes
              // sense while this field has focus.
              if ((e.key === "Enter" && !e.shiftKey) || ((e.metaKey || e.ctrlKey) && e.key === "Enter")) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Ask your documents…"
            className="max-h-40 flex-1 resize-none border-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-ink outline-none"
          />
          {isStreaming ? (
            <button
              type="button"
              title="Stop generating"
              data-testid="stop-generating-button"
              onClick={onStop}
              className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-accent text-on-accent transition-[opacity,background-color] hover:bg-accent-hover"
            >
              <Square size={14} strokeWidth={2} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              title="Send question"
              onClick={send}
              disabled={!draft.trim() || disabled}
              className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-accent text-on-accent transition-[opacity,background-color] hover:bg-accent-hover disabled:cursor-not-allowed"
              style={{ opacity: draft.trim() && !disabled ? 1 : 0.4 }}
            >
              <ArrowUp size={16} strokeWidth={2} />
            </button>
          )}
        </div>
        <p className="m-0 mt-2 text-center font-mono text-[10px] tracking-[0.08em] text-faint">
          <span className="text-accent">1</span>
          &nbsp; EVERY ANSWER CITES ITS SOURCE PAGES
        </p>
      </div>
    </div>
  );
}
