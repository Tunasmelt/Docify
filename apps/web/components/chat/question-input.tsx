"use client";

import * as React from "react";
import { ArrowUp } from "lucide-react";

export interface QuestionInputProps {
  onSend: (question: string) => void;
  disabled?: boolean;
}

export function QuestionInput({ onSend, disabled }: QuestionInputProps) {
  const [draft, setDraft] = React.useState("");

  function send() {
    const q = draft.trim();
    if (!q || disabled) return;
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
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Ask your documents…"
            className="max-h-40 flex-1 resize-none border-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-ink outline-none"
          />
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
        </div>
        <p className="m-0 mt-2 text-center font-mono text-[10px] tracking-[0.08em] text-faint">
          <span className="text-accent">1</span>
          &nbsp; EVERY ANSWER CITES ITS SOURCE PAGES
        </p>
      </div>
    </div>
  );
}
