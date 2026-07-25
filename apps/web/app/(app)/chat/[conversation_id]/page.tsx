"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar, WorkspaceBadge, MobileMenuButton } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMessageBubble, AssistantMessageBubble } from "@/components/chat/message-bubble";
import { LoadingStages } from "@/components/chat/loading-stages";
import { QuestionInput } from "@/components/chat/question-input";
import { SourcePanel } from "@/components/chat/source-panel";
import type { ChatMessage, Citation } from "@/lib/types/chat";
import { createClient } from "@/lib/supabase/browser";

const SEED_MESSAGES: ChatMessage[] = [
  {
    id: "m1",
    role: "user",
    text: "What are the termination notice requirements in the Meridian MSA?",
  },
  {
    id: "m2",
    role: "assistant",
    segments: [
      { type: "text", text: "Either party may terminate for convenience with 90 days’ written notice" },
      { type: "citation", citationId: "c1" },
      {
        type: "text",
        text: ", delivered to the counterparty’s registered agent. Termination for material breach requires 30 days’ notice with an opportunity to cure, and the cure period can be extended once by mutual written agreement",
      },
      { type: "citation", citationId: "c2" },
      { type: "text", text: ". Fees for work already performed survive termination." },
    ],
    citations: [
      {
        id: "c1",
        n: 1,
        documentName: "Master Services Agreement — Meridian",
        page: 14,
        verdict: "supported",
        excerpt:
          "“Either party may terminate this Agreement for convenience upon ninety (90) days’ prior written notice to the other party, delivered to the registered agent identified in Schedule A.”",
      },
      {
        id: "c2",
        n: 2,
        documentName: "Master Services Agreement — Meridian",
        page: 15,
        verdict: "partial",
        excerpt:
          "“In the event of material breach, the non-breaching party shall provide thirty (30) days’ written notice and a reasonable opportunity to cure.”",
      },
    ],
  },
  {
    id: "m3",
    role: "user",
    text: "How did operating margin trend in Q3?",
  },
  {
    id: "m4",
    role: "assistant",
    segments: [
      {
        type: "text",
        text: "Operating margin expanded from 18.2% in July to 21.6% by September, driven primarily by the services segment; the quarter-end figure is the highest in six quarters",
      },
      { type: "citation", citationId: "c3" },
      { type: "text", text: "." },
    ],
    citations: [
      {
        id: "c3",
        n: 1,
        documentName: "Q3 Financial Review",
        page: 7,
        verdict: "supported",
        isFigure: true,
        figureCaption: "FIGURE 4 · OPERATING MARGIN BY SEGMENT, Q3",
        excerpt:
          "Figure 4 charts monthly operating margin by segment across Q3, with the consolidated line reaching 21.6% in September.",
      },
    ],
  },
];

const RECENT_CONVERSATIONS = [
  { id: "conv_01", title: "Meridian MSA — termination terms", active: true },
  { id: "conv_02", title: "Handbook — PTO carryover rules", active: false },
  { id: "conv_03", title: "Deposition — witness timeline", active: false },
];

const USER = { initials: "AK", name: "Ana Kovač", email: "ana@firm.com" };

export default function ChatPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const [messages, setMessages] = React.useState<ChatMessage[]>(SEED_MESSAGES);
  const [loadingStage, setLoadingStage] = React.useState<number | null>(null);
  const [activeCitation, setActiveCitation] = React.useState<Citation | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const stageInterval = React.useRef<ReturnType<typeof setInterval>>();
  const askTimeout = React.useRef<ReturnType<typeof setTimeout>>();

  React.useEffect(() => {
    return () => {
      clearInterval(stageInterval.current);
      clearTimeout(askTimeout.current);
    };
  }, []);

  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loadingStage]);

  function ask(question: string) {
    // eslint-disable-next-line no-console
    console.log("[mock] POST /query —", question);
    setMessages((prev) => [...prev, { id: `m${Date.now()}`, role: "user", text: question }]);
    setLoadingStage(0);

    stageInterval.current = setInterval(() => {
      setLoadingStage((prev) => Math.min(2, (prev ?? 0) + 1));
    }, 1700);

    askTimeout.current = setTimeout(() => {
      clearInterval(stageInterval.current);
      setLoadingStage(null);
      setMessages((prev) => [
        ...prev,
        {
          id: `m${Date.now()}a`,
          role: "assistant",
          segments: [
            { type: "text", text: "This is a mock answer standing in for the real /query response" },
            { type: "citation", citationId: "mc" },
            { type: "text", text: "." },
          ],
          citations: [
            {
              id: "mc",
              n: 1,
              documentName: "Master Services Agreement — Meridian",
              page: 3,
              verdict: "supported",
              excerpt: "Mock excerpt — replaced by the real cited chunk.",
            },
          ],
        },
      ]);
    }, 5200);
  }

  const librarySection = (
    <>
      <div className="px-[22px] pb-2 pt-6 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
        Recent
      </div>
      <div className="flex flex-col gap-px px-3">
        {RECENT_CONVERSATIONS.map((conv) => (
          <div
            key={conv.id}
            className={`cursor-pointer truncate rounded-md px-2.5 py-1.5 text-[12.5px] hover:bg-panel-hover hover:text-ink ${
              conv.active ? "bg-panel-hover text-ink" : "text-muted"
            }`}
          >
            {conv.title}
          </div>
        ))}
      </div>
    </>
  );

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  const isEmpty = messages.length === 0;

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
              <span className="truncate text-sm font-semibold">
                Meridian MSA — termination terms
              </span>
              <WorkspaceBadge>2 DOCS IN SCOPE</WorkspaceBadge>
            </>
          }
          right={<ThemeToggle />}
        />
        <main ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          {isEmpty ? (
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
                  <AssistantMessageBubble
                    key={msg.id}
                    message={msg}
                    activeCitationId={activeCitation?.id ?? null}
                    onOpenCitation={setActiveCitation}
                  />
                )
              )}
              {loadingStage !== null ? <LoadingStages stage={loadingStage} /> : null}
            </div>
          )}
        </main>
        <QuestionInput onSend={ask} disabled={loadingStage !== null} />
        <SourcePanel
          citation={activeCitation}
          onClose={() => setActiveCitation(null)}
          onOpenInDocument={(citation) =>
            // eslint-disable-next-line no-console
            console.log("[mock] open document at page", citation.page)
          }
        />
      </div>
    </div>
  );
}
