"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar, WorkspaceBadge, MobileMenuButton } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { ConversationCard, type ConversationCardData } from "@/components/conversations/conversation-card";
import { createClient } from "@/lib/supabase/browser";
import { ApiError } from "@/lib/api/client";
import { listConversations, type ApiConversation } from "@/lib/api/conversations";
import { listDocuments } from "@/lib/api/documents";

const USER = { initials: "AK", name: "Ana Kovač", email: "ana@firm.com" };

function formatUpdatedAt(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" })
    .format(new Date(iso))
    .toUpperCase();
}

// GET /conversations returns document_ids, not names (API_CONTRACT.md) —
// resolved here against the user's real document list, the same
// composition the sidebar's own document picker already relies on,
// rather than a new backend field. A document deleted since a
// conversation was scoped to it simply drops out of the resolved name
// list (documentNames: [] renders "No documents" — an honest reflection
// of what's left, not a broken lookup).
function toCardData(conv: ApiConversation, docNamesById: Map<string, string>): ConversationCardData {
  return {
    id: conv.id,
    title: conv.title ?? "Untitled conversation",
    documentNames: conv.document_ids.map((id) => docNamesById.get(id)).filter((name): name is string => !!name),
    messageCount: conv.message_count,
    updatedAtLabel: formatUpdatedAt(conv.updated_at),
  };
}

export default function ConversationListPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const [conversations, setConversations] = React.useState<ApiConversation[]>([]);
  const [docNamesById, setDocNamesById] = React.useState<Map<string, string>>(new Map());
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    Promise.all([listConversations(), listDocuments()])
      .then(([conversationsResult, documentsResult]) => {
        if (cancelled) return;
        setConversations(conversationsResult.conversations);
        setDocNamesById(new Map(documentsResult.documents.map((d) => [d.id, d.filename])));
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

  async function handleRetryLoad() {
    setLoading(true);
    setLoadError(null);
    try {
      const [conversationsResult, documentsResult] = await Promise.all([listConversations(), listDocuments()]);
      setConversations(conversationsResult.conversations);
      setDocNamesById(new Map(documentsResult.documents.map((d) => [d.id, d.filename])));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load your conversations.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  const cardConversations = conversations.map((conv) => toCardData(conv, docNamesById));

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
          <div className="mx-auto max-w-[760px] animate-fade-up px-6 py-10">
            <div className="mb-8 flex items-baseline justify-between">
              <h1 className="m-0 font-serif text-[28px] font-medium">
                Conversations
                <sup className="text-sm font-normal text-accent">1</sup>
              </h1>
              {!loading && !loadError && cardConversations.length > 0 ? (
                <span className="font-mono text-[11px] tracking-[0.08em] text-faint">
                  {cardConversations.length} {cardConversations.length === 1 ? "CONVERSATION" : "CONVERSATIONS"}
                </span>
              ) : null}
            </div>

            {loading ? (
              <div className="overflow-hidden rounded-lg border border-line bg-drop-bg">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="flex items-center gap-4 border-b border-line px-[18px] py-3.5 last:border-b-0">
                    <div className="h-9 w-7 flex-shrink-0 rounded-[3px] bg-panel-active" />
                    <div className="min-w-0 flex-1">
                      <div className="h-3.5 w-2/3 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                      <div className="mt-2 h-2.5 w-1/3 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                    </div>
                  </div>
                ))}
              </div>
            ) : loadError ? (
              <div className="mt-14 text-center">
                <p className="m-0 font-serif text-lg text-muted">Couldn't load your conversations</p>
                <p className="mx-auto mt-1 max-w-[320px] text-sm leading-relaxed text-faint">{loadError}</p>
                <Button type="button" variant="outline" size="sm" className="mt-4" onClick={handleRetryLoad}>
                  Try again
                </Button>
              </div>
            ) : cardConversations.length === 0 ? (
              <div className="mt-14 text-center">
                <p className="m-0 font-serif text-lg text-muted">No conversations yet.</p>
                <p className="mx-auto mt-1 max-w-[320px] text-sm leading-relaxed text-faint">
                  Select one or more ready documents from Documents and ask a question to start one.
                </p>
              </div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-line bg-drop-bg">
                {cardConversations.map((conv) => (
                  <ConversationCard key={conv.id} conversation={conv} />
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
