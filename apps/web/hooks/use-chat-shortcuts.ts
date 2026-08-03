"use client";

import * as React from "react";

export interface ChatShortcutHandlers {
  /** ⌘/Ctrl+K — jumps to the flow that starts a new conversation. There
   * is no standalone "new conversation" action yet (batch 1 is UI-only,
   * zero backend changes) — /documents' document-picker + "Ask about
   * these" IS that flow today, so this is where the shortcut sends the
   * user; it becomes a more direct action once that exists. */
  onNewConversation: () => void;
  /** Esc — closes the source panel if one is open. Deliberately a no-op
   * otherwise: does NOT clear the in-progress question draft or
   * navigate away. Esc-while-typing silently discarding a half-written
   * question would be a surprising, easy-to-trigger data loss, not a
   * convenience — closing an already-open overlay is the only behavior
   * worth binding here. */
  onEscape: () => void;
}

/** Chat-page keyboard shortcuts (batch 1, item 5). Both bindings here
 * are modifier combinations (⌘/Ctrl+K) or a non-printable key (Esc) —
 * neither can corrupt text a user is mid-typing the way stealing a
 * printable keystroke would, so both are safe to keep global (fire
 * regardless of which element currently has focus, including inside
 * the question textarea) rather than scoped to one input — matching
 * common command-palette/overlay-dismiss conventions elsewhere (Slack,
 * Linear: Cmd+K and Esc always work).
 *
 * ⌘/Ctrl+Enter (send) is deliberately NOT handled here — it only makes
 * sense while the question textarea has focus, so it's wired locally in
 * QuestionInput's own onKeyDown instead, next to the existing plain-
 * Enter-to-send handling it already has. */
export function useChatShortcuts({ onNewConversation, onEscape }: ChatShortcutHandlers): void {
  const onNewConversationRef = React.useRef(onNewConversation);
  const onEscapeRef = React.useRef(onEscape);
  onNewConversationRef.current = onNewConversation;
  onEscapeRef.current = onEscape;

  React.useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        // preventDefault: Ctrl+K is a real browser binding in some
        // browsers (focus the address bar / quick search) — this app
        // action takes priority while the chat page is open.
        e.preventDefault();
        onNewConversationRef.current();
        return;
      }
      if (e.key === "Escape") {
        onEscapeRef.current();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);
}
