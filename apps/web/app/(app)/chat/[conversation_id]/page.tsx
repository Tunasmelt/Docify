"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar, WorkspaceBadge, MobileMenuButton } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMessageBubble, AssistantMessageBubble } from "@/components/chat/message-bubble";
import { LoadingStages, type StreamingStage } from "@/components/chat/loading-stages";
import { QuestionInput } from "@/components/chat/question-input";
import { SourcePanel } from "@/components/chat/source-panel";
import type { ChatMessage, Citation } from "@/lib/types/chat";
import { createClient } from "@/lib/supabase/browser";
import { askQuestionStream } from "@/lib/api/query";
import { buildAssistantMessage } from "@/lib/chat/parse-message";
import { getConversationMessages, listConversations, type ApiConversation } from "@/lib/api/conversations";
import { ApiError } from "@/lib/api/client";

const USER = { initials: "AK", name: "Ana Kovač", email: "ana@firm.com" };

function truncateTitle(text: string): string {
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

export default function ChatPage({ params }: { params: { conversation_id: string } }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = React.useMemo(() => createClient(), []);

  const isNew = params.conversation_id === "new";
  const initialDocumentIds = React.useMemo(
    () => (isNew ? (searchParams.get("docs")?.split(",").filter(Boolean) ?? []) : []),
    [isNew, searchParams]
  );

  const [conversationId, setConversationId] = React.useState<string | null>(isNew ? null : params.conversation_id);
  const [documentIds, setDocumentIds] = React.useState<string[]>(initialDocumentIds);
  const [title, setTitle] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [loadingHistory, setLoadingHistory] = React.useState(!isNew);
  const [notFound, setNotFound] = React.useState(false);
  const [historyError, setHistoryError] = React.useState<string | null>(null);
  const [askError, setAskError] = React.useState<string | null>(null);
  // FEAT-016 (2026-07-27): replaces the old timer-driven `loadingStage`
  // index with real SSE-signal state. `streamingId` is the in-progress
  // assistant message's id (null when nothing is streaming — also what
  // disables the question input); `streamingStage` is only meaningful
  // while streamingId is set, and covers exactly the two gaps that have
  // no visible content of their own: before the first token ("retrieving")
  // and after the last token, while verification runs ("verifying").
  // While tokens are actively arriving, streamingStage is null — the
  // growing real text IS the progress indicator.
  const [streamingId, setStreamingId] = React.useState<string | null>(null);
  const [streamingStage, setStreamingStage] = React.useState<StreamingStage | null>(null);
  const [activeCitation, setActiveCitation] = React.useState<Citation | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const [recentConversations, setRecentConversations] = React.useState<ApiConversation[] | null>(null);

  const scrollRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streamingStage]);

  // Real conversation history — omitted entirely for a fresh "new" chat,
  // since there is nothing to load until the first real POST /query
  // creates one.
  React.useEffect(() => {
    if (isNew) return;
    let cancelled = false;
    setLoadingHistory(true);
    setNotFound(false);
    setHistoryError(null);
    getConversationMessages(params.conversation_id)
      .then((result) => {
        if (cancelled) return;
        setMessages(result.messages);
        setDocumentIds(result.conversation.document_ids);
        setTitle(result.conversation.title);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setHistoryError(err instanceof ApiError ? err.message : "Couldn't load this conversation.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isNew, params.conversation_id]);

  // The sidebar's "Recent" list — independent of which conversation is
  // currently open, so a stale list here never blocks the main thread of
  // reading/asking questions.
  React.useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((result) => {
        if (!cancelled) setRecentConversations(result.conversations);
      })
      .catch(() => {
        if (!cancelled) setRecentConversations([]);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  async function ask(question: string) {
    if (documentIds.length === 0) {
      setAskError("Select at least one ready document from Documents before asking a question.");
      return;
    }
    setAskError(null);
    const userMessageId = `local-${Date.now()}`;
    const streamId = `stream-${userMessageId}`;
    const optimisticUserMessage: ChatMessage = { id: userMessageId, role: "user", text: question };
    setMessages((prev) => [...prev, optimisticUserMessage]);
    setStreamingId(streamId);
    setStreamingStage("retrieving");

    // Local accumulator, not state — read/written synchronously inside
    // the SSE callbacks below (all of which fire strictly in sequence
    // for one stream, never concurrently), then flushed into `messages`
    // state on every token so the visible text grows in real time.
    let accumulated = "";
    let resolvedConversationId: string | null = null;

    try {
      await askQuestionStream(question, documentIds, conversationId, {
        onRetrieving: () => setStreamingStage("retrieving"),
        onToken: (text) => {
          accumulated += text;
          setStreamingStage(null);
          setMessages((prev) => {
            // First token: the placeholder bubble doesn't exist yet
            // (kept out of `messages` entirely during "retrieving" so
            // LoadingStages' skeleton renders in its place, not
            // alongside an empty bubble) — insert it now.
            const exists = prev.some((m) => m.id === streamId);
            const rebuilt = buildAssistantMessage(streamId, accumulated, []);
            return exists ? prev.map((m) => (m.id === streamId ? rebuilt : m)) : [...prev, rebuilt];
          });
        },
        onVerifying: () => setStreamingStage("verifying"),
        onCitationsResolved: (event) => {
          resolvedConversationId = event.conversation_id;
          const finalMessage = buildAssistantMessage(streamId, event.answer, event.citations);
          setMessages((prev) => prev.map((m) => (m.id === streamId ? finalMessage : m)));
        },
        onDone: () => {
          setStreamingId(null);
          setStreamingStage(null);
          if (!title) setTitle(truncateTitle(question));
          if (resolvedConversationId && !conversationId) {
            setConversationId(resolvedConversationId);
            // Replace, not push — "new" was never a real conversation
            // state worth returning to via back-navigation.
            router.replace(`/chat/${resolvedConversationId}`);
          }
        },
        onError: (message) => {
          setStreamingId(null);
          setStreamingStage(null);
          if (accumulated.length === 0) {
            // Nothing was ever shown — behave like a full failure
            // (matches the old non-streaming error path) so retrying
            // doesn't leave a duplicated question behind.
            setMessages((prev) => prev.filter((m) => m.id !== userMessageId && m.id !== streamId));
          }
          // Partial text (if any) is deliberately left in place — a
          // half-finished answer with a clear error notice below it is
          // the required "sensible error state," never a silent hang or
          // an unindicated partial answer.
          setAskError(message);
        },
      });
    } catch (err) {
      // Failed before any streaming began at all (network error opening
      // the connection, or a non-2xx validation/ownership/auth response)
      // — identical to the old non-streaming failure path.
      setStreamingId(null);
      setStreamingStage(null);
      setMessages((prev) => prev.filter((m) => m.id !== userMessageId && m.id !== streamId));
      setAskError(
        err instanceof ApiError ? err.message : "Something went wrong asking that question. Try again."
      );
    }
  }

  const librarySection = (
    <>
      <div className="px-[22px] pb-2 pt-6 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
        Recent
      </div>
      <div className="flex flex-col gap-px px-3">
        {recentConversations === null ? (
          <div className="flex flex-col gap-2 px-2.5 py-1">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-4 w-full animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
            ))}
          </div>
        ) : recentConversations.length === 0 ? (
          <div className="px-2.5 py-1 text-[12.5px] text-faint">No conversations yet.</div>
        ) : (
          recentConversations.map((conv) => (
            <a
              key={conv.id}
              href={`/chat/${conv.id}`}
              className={`truncate rounded-md px-2.5 py-1.5 text-[12.5px] no-underline hover:bg-panel-hover hover:text-ink ${
                conv.id === conversationId ? "bg-panel-hover text-ink" : "text-muted"
              }`}
            >
              {conv.title ?? "Untitled conversation"}
            </a>
          ))
        )}
      </div>
    </>
  );

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  const isEmpty = messages.length === 0;
  const headerTitle = title ?? messages.find((m) => m.role === "user")?.text ?? "New conversation";

  return (
    <div className="grid h-screen grid-cols-1 overflow-hidden bg-bg text-ink md:grid-cols-[248px_1fr]">
      <Sidebar
        librarySection={librarySection}
        user={USER}
        mobileOpen={mobileMenuOpen}
        onMobileClose={() => setMobileMenuOpen(false)}
        onSignOut={handleSignOut}
      />
      <div className="relative flex min-w-0 flex-col">
        <Topbar
          left={
            <>
              <MobileMenuButton onClick={() => setMobileMenuOpen(true)} />
              <span className="truncate text-sm font-semibold">{truncateTitle(headerTitle)}</span>
              {documentIds.length > 0 ? (
                <WorkspaceBadge>
                  {documentIds.length} {documentIds.length === 1 ? "DOC" : "DOCS"} IN SCOPE
                </WorkspaceBadge>
              ) : null}
            </>
          }
          right={<ThemeToggle />}
        />
        <main ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          {notFound ? (
            <div className="flex h-full items-center justify-center px-6 text-center">
              <div>
                <p className="m-0 font-serif text-lg text-muted">Conversation not found</p>
                <a href="/chat" className="mt-2 inline-block text-sm text-accent">
                  Back to conversations
                </a>
              </div>
            </div>
          ) : historyError ? (
            <div className="flex h-full items-center justify-center px-6 text-center">
              <p className="m-0 text-sm text-muted">{historyError}</p>
            </div>
          ) : loadingHistory ? (
            <div className="mx-auto flex max-w-[720px] flex-col gap-6 px-6 py-8">
              {[0, 1].map((i) => (
                <div key={i} className="flex flex-col gap-2">
                  <div className="h-3 w-4/5 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                  <div className="h-3 w-3/5 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                </div>
              ))}
            </div>
          ) : isEmpty ? (
            <div className="flex h-full items-center justify-center px-6">
              <div className="max-w-[420px] animate-fade-up text-center">
                <div className="font-serif text-4xl leading-none text-accent">¶</div>
                <h1 className="m-0 mb-2 mt-4 font-serif text-[28px] font-medium leading-[1.3]">
                  Ask your library anything
                  <sup className="text-sm font-normal text-accent">1</sup>
                </h1>
                <p className="m-0 text-[15px] leading-relaxed text-muted">
                  Questions are answered from your documents — nothing else.
                </p>
                <p className="m-0 mt-6 font-mono text-[11px] tracking-[0.04em] text-faint">
                  <span className="text-accent">1</span>
                  &nbsp; Answers carry footnotes. Click one to see the exact
                  page it came from.
                </p>
              </div>
            </div>
          ) : (
            <div className="mx-auto flex max-w-[720px] flex-col gap-8 px-6 py-8">
              {messages.map((msg) =>
                msg.role === "user" ? (
                  <UserMessageBubble key={msg.id} message={msg} />
                ) : (
                  <React.Fragment key={msg.id}>
                    <AssistantMessageBubble
                      message={msg}
                      activeCitationId={activeCitation?.id ?? null}
                      onOpenCitation={setActiveCitation}
                    />
                    {msg.id === streamingId && streamingStage === "verifying" ? (
                      <LoadingStages stage="verifying" />
                    ) : null}
                  </React.Fragment>
                )
              )}
              {streamingId !== null && streamingStage === "retrieving" ? (
                <LoadingStages stage="retrieving" />
              ) : null}
            </div>
          )}
        </main>
        {askError ? (
          <p className="mx-6 mb-1 text-center text-sm text-destructive">{askError}</p>
        ) : null}
        <QuestionInput onSend={ask} disabled={streamingId !== null || loadingHistory || notFound} />
        <SourcePanel
          citation={activeCitation}
          onClose={() => setActiveCitation(null)}
          onOpenInDocument={(citation) =>
            // Real per-page document viewing is Phase 4+ scope (not part
            // of this wiring pass — no route/viewer exists yet to open).
            // eslint-disable-next-line no-console
            console.log("[not yet built] open document at location", citation.location)
          }
        />
      </div>
    </div>
  );
}
