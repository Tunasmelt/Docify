"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CitationMarker } from "@/components/chat/citation-marker";
import { useRelativeTime } from "@/hooks/use-relative-time";
import { citationPlaceholder, remarkCitationMarkers } from "@/lib/chat/remark-citation-markers";
import type { AssistantMessage, Citation, UserMessage } from "@/lib/types/chat";

const COPY_CONFIRM_MS = 1500;

function formatAbsoluteTimestamp(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** Relative time label, real timestamp on hover (batch 1, item 2) — one
 * shared component so user and assistant bubbles render it identically. */
function MessageTimestamp({ createdAt, align }: { createdAt: string; align: "left" | "right" }) {
  const relative = useRelativeTime(createdAt);
  return (
    <p
      title={formatAbsoluteTimestamp(createdAt)}
      className={`m-0 mt-1 font-mono text-[10px] tracking-[0.04em] text-faint ${align === "right" ? "text-right" : ""}`}
    >
      {relative}
    </p>
  );
}

export function UserMessageBubble({ message }: { message: UserMessage }) {
  return (
    <div className="flex flex-col items-end">
      <div
        data-testid="user-message"
        className="max-w-[70%] rounded-[12px_12px_4px_12px] bg-panel-active px-4 py-3 text-[15px] leading-relaxed"
      >
        {message.text}
      </div>
      <MessageTimestamp createdAt={message.createdAt} align="right" />
    </div>
  );
}

export interface AssistantMessageBubbleProps {
  message: AssistantMessage;
  activeCitationId: string | null;
  onOpenCitation: (citation: Citation) => void;
  /** True while this exact message is the one actively streaming
   * (batch 1, item 3) — drives the trailing cursor. */
  isStreaming?: boolean;
}

// buildAssistantMessage() (lib/chat/parse-message.ts) already does the
// one real citation-bracket parse this project trusts, producing
// message.segments: an alternating [text, citation, text, citation, ...]
// array. Markdown needs to see the message as ONE document (so headings/
// lists/multi-paragraph structure — all real, observed Gemini output
// shapes — parse correctly, including when a citation lands inside a
// list item), not one independent parse per segment (which breaks block
// continuity at every citation boundary). Rejoining segments into a
// single string with an inert placeholder in place of each "citation"
// segment gets both: one real markdown parse, and citation markers
// spliced back in at their exact original position via
// remarkCitationMarkers — without ever re-deriving which brackets are
// real citations a second time.
function joinSegmentsWithPlaceholders(message: AssistantMessage): { content: string; citationByPlaceholder: Map<number, string> } {
  const citationByPlaceholder = new Map<number, string>();
  let content = "";
  let placeholderIndex = 0;
  for (const seg of message.segments) {
    if (seg.type === "text") {
      content += seg.text;
    } else {
      citationByPlaceholder.set(placeholderIndex, seg.citationId);
      content += citationPlaceholder(placeholderIndex);
      placeholderIndex += 1;
    }
  }
  return { content, citationByPlaceholder };
}

export function AssistantMessageBubble({
  message,
  activeCitationId,
  onOpenCitation,
  isStreaming = false,
}: AssistantMessageBubbleProps) {
  const [copied, setCopied] = React.useState(false);
  const copyResetRef = React.useRef<ReturnType<typeof setTimeout>>();

  React.useEffect(() => () => clearTimeout(copyResetRef.current), []);

  async function handleCopy() {
    try {
      // Raw answer text (task's own wording) — message.rawText is the
      // literal string buildAssistantMessage() was given, before
      // markdown rendering and before citation-marker splicing, not the
      // rendered DOM's innerText (which would include footnote digits
      // and citation-list rows that were never part of the answer).
      await navigator.clipboard.writeText(message.rawText);
      setCopied(true);
      clearTimeout(copyResetRef.current);
      copyResetRef.current = setTimeout(() => setCopied(false), COPY_CONFIRM_MS);
    } catch {
      // Clipboard permission denied/unavailable — no toast system
      // exists anywhere else in this chat UI to surface a failure
      // through, so this silently no-ops rather than inventing one for
      // a single button.
    }
  }

  const citationsById = React.useMemo(
    () => Object.fromEntries(message.citations.map((c) => [c.id, c])),
    [message.citations]
  );
  const { content, citationByPlaceholder } = React.useMemo(
    () => joinSegmentsWithPlaceholders(message),
    [message]
  );

  // react-markdown's Components type is keyed by keyof JSX.IntrinsicElements
  // (real HTML tags only) — "citation-marker" is never a real element, just
  // the tag name remarkCitationMarkers emits via data.hName as react-
  // markdown's own documented extension point for splicing arbitrary React
  // components into the parsed tree, so the cast below is expected, not a
  // type-safety hole (every other key is still fully checked against real
  // JSX.IntrinsicElements props).
  type Children = { children?: React.ReactNode };
  const markdownComponents: Components = {
    p: ({ children }: Children) => <p className="m-0 mt-3 first:mt-0">{children}</p>,
    strong: ({ children }: Children) => <strong className="font-semibold text-ink">{children}</strong>,
    em: ({ children }: Children) => <em className="italic">{children}</em>,
    code: ({ children }: Children) => (
      <code className="rounded bg-panel-active px-1 py-0.5 font-mono text-[0.85em] text-ink">{children}</code>
    ),
    pre: ({ children }: Children) => (
      <pre className="m-0 mt-3 overflow-x-auto rounded-md bg-panel-active p-3 font-mono text-[0.85em]">{children}</pre>
    ),
    ul: ({ children }: Children) => <ul className="m-0 mt-3 list-disc space-y-1 pl-5">{children}</ul>,
    ol: ({ children }: Children) => <ol className="m-0 mt-3 list-decimal space-y-1 pl-5">{children}</ol>,
    li: ({ children }: Children) => <li className="pl-0.5">{children}</li>,
    h1: ({ children }: Children) => <h1 className="m-0 mt-4 font-serif text-[20px] font-medium text-ink first:mt-0">{children}</h1>,
    h2: ({ children }: Children) => <h2 className="m-0 mt-4 font-serif text-[18px] font-medium text-ink first:mt-0">{children}</h2>,
    h3: ({ children }: Children) => <h3 className="m-0 mt-3 font-serif text-[16px] font-medium text-ink first:mt-0">{children}</h3>,
    h4: ({ children }: Children) => <h4 className="m-0 mt-3 text-[15px] font-semibold text-ink first:mt-0">{children}</h4>,
    blockquote: ({ children }: Children) => (
      <blockquote className="m-0 mt-3 border-l-2 border-line pl-3 text-muted">{children}</blockquote>
    ),
    a: ({ children, href }: Children & { href?: string }) => (
      <a href={href} target="_blank" rel="noreferrer" className="text-accent underline-offset-2 hover:underline">
        {children}
      </a>
    ),
    hr: () => <hr className="my-4 border-line" />,
    table: ({ children }: Children) => (
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-[0.9em]">{children}</table>
      </div>
    ),
    th: ({ children }: Children) => <th className="border border-line px-2 py-1 text-left font-semibold">{children}</th>,
    td: ({ children }: Children) => <td className="border border-line px-2 py-1">{children}</td>,
    // Custom element emitted by remarkCitationMarkers via data.hName —
    // never a real HTML tag, just react-markdown's own documented
    // extension point for splicing arbitrary React components into the
    // parsed tree at the right position.
    "citation-marker": ({ placeholderindex }: { placeholderindex?: number }) => {
      if (placeholderindex === undefined) return null;
      const citationId = citationByPlaceholder.get(placeholderindex);
      const citation = citationId ? citationsById[citationId] : undefined;
      // No resolved citation for this placeholder (streaming,
      // citations=[] until citations-resolved arrives, or a
      // hallucinated marker's bracket text made it into a plain text
      // segment already — either way buildAssistantMessage() already
      // decided this position isn't a real citation) — render nothing
      // extra; the placeholder only ever exists for citation segments
      // in the first place, so citationId itself should always
      // resolve, but citation may not if message.citations hasn't
      // caught up to segments yet.
      if (!citation) return null;
      return <CitationMarker citation={citation} active={activeCitationId === citation.id} onOpen={onOpenCitation} />;
    },
  } as Components;

  return (
    <div data-testid="assistant-message" className="group relative max-w-[85%]">
      <button
        type="button"
        data-testid="copy-message-button"
        title={copied ? "Copied" : "Copy message"}
        onClick={handleCopy}
        // Hidden until hover/focus of the bubble (group-hover) or
        // focus of the button itself (focus-visible, keyboard/a11y
        // path) — matches the task's "on hover/focus" spec exactly,
        // not just mouse hover.
        className="absolute -top-1.5 right-0 flex h-7 w-7 items-center justify-center rounded-md text-faint opacity-0 transition-opacity hover:bg-panel hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
      >
        {copied ? <Check size={14} strokeWidth={2} /> : <Copy size={14} strokeWidth={2} />}
      </button>
      <div className="markdown-body text-[15px] leading-[1.75]">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitationMarkers]} components={markdownComponents}>
          {content}
        </ReactMarkdown>
        {isStreaming ? (
          // Pulsing caret while tokens are actively arriving (batch 1,
          // item 3) — reuses the existing pulse-dot animation (already
          // used for the "retrieving/verifying" indicator dot) rather
          // than inventing a new blink keyframe. Purely reactive to
          // isStreaming (itself derived from real SSE state in the
          // page, never a timer) so it disappears exactly on `done`,
          // including for a message that completes in one fast burst —
          // there's no separate timer here that could leave it
          // dangling or flicker independently of the real stream state.
          <span
            aria-hidden="true"
            data-testid="streaming-cursor"
            className="ml-0.5 inline-block h-[1em] w-[2px] animate-pulse-dot align-text-bottom bg-accent"
          />
        ) : null}
      </div>
      <MessageTimestamp createdAt={message.createdAt} align="left" />
      {message.citations.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-x-[18px] gap-y-1 border-t border-line pt-2">
          {message.citations.map((citation) => (
            <button
              key={citation.id}
              type="button"
              onClick={() => onOpenCitation(citation)}
              className="flex items-center gap-1 border-none bg-transparent p-0 font-mono text-[11px] tracking-[0.02em] text-faint hover:text-muted"
            >
              <span
                style={{
                  color:
                    citation.verdict === "partial"
                      ? "var(--amber)"
                      : citation.verdict === "unverified"
                        ? "var(--muted)"
                        : "var(--accent)",
                }}
              >
                {citation.n}
              </span>
              {citation.documentName}
              {/* location is null for DOCX/HTML sources (no real page
                  concept — FEAT-020) — omit rather than show a false
                  page number for every citation in the document. */}
              {citation.location
                ? ` · ${citation.location.kind === "page" ? "P" : "S"}.${citation.location.number}`
                : ""}
              {citation.verdict === "partial" ? " · partial" : ""}
              {citation.verdict === "unverified" ? " · unverified" : ""}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
