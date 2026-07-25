"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar, WorkspaceBadge, MobileMenuButton } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { createClient } from "@/lib/supabase/browser";
import { ApiError } from "@/lib/api/client";
import { listConversations, type ApiConversation } from "@/lib/api/conversations";

const USER = { initials: "AK", name: "Ana Kovač", email: "ana@firm.com" };

// PLACEHOLDER (flagged explicitly, per this task's own instructions):
// this page exists to give the sidebar's already-wired "Conversations"
// nav link (-> /chat) somewhere real to land, and to unblock live
// end-to-end wiring/testing of the chat flow. It intentionally matches
// this project's design tokens loosely rather than going through a full
// Claude Design pass — no pagination UI (next_cursor is fetched but
// unused beyond the first page), no search/filter, no delete. Revisit
// with a real design pass once the rest of the chat flow is proven.
function formatUpdatedAt(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" })
    .format(new Date(iso))
    .toUpperCase();
}

export default function ConversationListPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const [conversations, setConversations] = React.useState<ApiConversation[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((result) => {
        if (!cancelled) setConversations(result.conversations);
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : "Couldn't load your conversations.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="grid min-h-screen grid-cols-1 bg-bg text-ink md:grid-cols-[248px_1fr]">
      <Sidebar user={USER} mobileOpen={mobileMenuOpen} onMobileClose={() => setMobileMenuOpen(false)} onSignOut={handleSignOut} />
      <div className="flex min-w-0 flex-col">
        <Topbar
          left={
            <>
              <MobileMenuButton onClick={() => setMobileMenuOpen(true)} />
              <span className="truncate text-sm font-semibold">Acme Legal</span>
              <WorkspaceBadge>WORKSPACE</WorkspaceBadge>
            </>
          }
          right={<ThemeToggle />}
        />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[760px] px-6 py-10">
            <h1 className="m-0 mb-8 font-serif text-[28px] font-medium">
              Conversations
              <sup className="text-sm font-normal text-accent">1</sup>
            </h1>

            {loading ? (
              <div className="flex flex-col gap-3">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-16 animate-shimmer rounded-lg border border-line bg-sk-grad-c bg-[length:400px_100%]" />
                ))}
              </div>
            ) : loadError ? (
              <p className="text-sm text-muted">{loadError}</p>
            ) : conversations.length === 0 ? (
              <div className="mt-14 text-center">
                <p className="m-0 font-serif text-lg text-muted">No conversations yet.</p>
                <p className="mx-auto mt-1 max-w-[320px] text-sm leading-relaxed text-faint">
                  Select one or more ready documents from Documents and ask a question to start one.
                </p>
              </div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-line">
                {conversations.map((conv) => (
                  <a
                    key={conv.id}
                    href={`/chat/${conv.id}`}
                    className="flex items-center justify-between border-b border-line px-[18px] py-3.5 no-underline last:border-b-0 hover:bg-panel-hover"
                  >
                    <div className="min-w-0">
                      <p className="m-0 truncate font-serif text-[15px] font-medium text-ink">
                        {conv.title ?? "Untitled conversation"}
                      </p>
                      <p className="m-0 mt-0.5 font-mono text-[11px] text-faint">
                        {conv.message_count} {conv.message_count === 1 ? "MESSAGE" : "MESSAGES"} · {formatUpdatedAt(conv.updated_at)}
                      </p>
                    </div>
                  </a>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
