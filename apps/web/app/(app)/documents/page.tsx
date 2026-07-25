"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar, WorkspaceBadge, MobileMenuButton } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { UploadZone, type UploadedDocument } from "@/components/documents/upload-zone";
import { DocumentCard, type DocumentCardData } from "@/components/documents/document-card";
import { DeleteConfirmDialog } from "@/components/documents/delete-confirm-dialog";
import { createClient } from "@/lib/supabase/browser";
import {
  ApiError,
  deleteDocument,
  listDocuments,
  type ApiDocument,
} from "@/lib/api/documents";
import type { DocumentStatus } from "@/lib/status-styles";

const USER = { initials: "AK", name: "Ana Kovač", email: "ana@firm.com" };

const NON_TERMINAL_STATUSES: DocumentStatus[] = ["uploaded", "parsing", "embedded"];
const POLL_BASE_INTERVAL_MS = 2000;
const POLL_MAX_INTERVAL_MS = 60000;
const POLL_BACKOFF_FACTOR = 1.5;

function formatDocDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
    .format(new Date(iso))
    .toUpperCase();
}

function toCardData(doc: ApiDocument): DocumentCardData {
  return {
    id: doc.id,
    filename: doc.filename,
    pages: doc.page_count,
    date: formatDocDate(doc.created_at),
    status: doc.status,
  };
}

export default function DocumentsPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const [docs, setDocs] = React.useState<ApiDocument[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [deleteId, setDeleteId] = React.useState<string | null>(null);
  const [deleteError, setDeleteError] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);

  const pollTimeoutRef = React.useRef<ReturnType<typeof setTimeout>>();
  const pollIntervalRef = React.useRef(POLL_BASE_INTERVAL_MS);

  // Guards against a real, live-confirmed race: a poll's GET /documents can
  // still be in flight when the user deletes (or a fresh upload optimistically
  // inserts) a document. If that stale response is applied after the fact, it
  // silently overwrites the newer state — confirmed live, a document deleted
  // via a real 204 response reappeared in the UI because an in-flight poll
  // response landed a moment later carrying the pre-delete snapshot. Every
  // authoritative docs update bumps this; pollTick discards its own result if
  // the generation moved on while it was awaiting the network.
  const docsGenerationRef = React.useRef(0);

  const deleteTarget = docs.find((d) => d.id === deleteId) ?? null;

  function commitDocs(newDocs: ApiDocument[]) {
    docsGenerationRef.current += 1;
    setDocs(newDocs);
  }

  const pollTick = React.useCallback(async () => {
    const myGeneration = docsGenerationRef.current;
    try {
      const result = await listDocuments();
      if (docsGenerationRef.current !== myGeneration) {
        // A delete/upload/retry committed newer state while this request
        // was in flight — do not resurrect what it just changed.
        return;
      }
      commitDocs(result.documents);
      const stillPending = result.documents.some((d) => NON_TERMINAL_STATUSES.includes(d.status));
      if (!stillPending) {
        pollIntervalRef.current = POLL_BASE_INTERVAL_MS;
        return;
      }
      pollIntervalRef.current = Math.min(
        pollIntervalRef.current * POLL_BACKOFF_FACTOR,
        POLL_MAX_INTERVAL_MS
      );
    } catch {
      // A network hiccup mid-poll shouldn't silently give up forever —
      // keep polling, just back off same as a slow-to-finish document.
      // (A real 401 mid-poll is handled inside listDocuments()'s own
      // apiFetch, which force-redirects before this catch ever runs.)
      pollIntervalRef.current = Math.min(
        pollIntervalRef.current * POLL_BACKOFF_FACTOR,
        POLL_MAX_INTERVAL_MS
      );
    }
    pollTimeoutRef.current = setTimeout(pollTick, pollIntervalRef.current);
  }, []);

  const startPolling = React.useCallback(() => {
    clearTimeout(pollTimeoutRef.current);
    pollIntervalRef.current = POLL_BASE_INTERVAL_MS;
    pollTimeoutRef.current = setTimeout(pollTick, pollIntervalRef.current);
  }, [pollTick]);

  React.useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const result = await listDocuments();
        if (cancelled) return;
        commitDocs(result.documents);
        setLoadError(null);
        if (result.documents.some((d) => NON_TERMINAL_STATUSES.includes(d.status))) {
          startPolling();
        }
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err.message : "Couldn't load your documents.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      clearTimeout(pollTimeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A selected document can stop being valid to select without the user
  // touching the checkbox themselves — deleted by this same session, or
  // (less likely mid-poll) no longer 'ready'. Prune rather than let a
  // stale id silently ride along into the next "Ask about these".
  React.useEffect(() => {
    const readyIds = new Set(docs.filter((d) => d.status === "ready").map((d) => d.id));
    setSelectedIds((prev) => prev.filter((id) => readyIds.has(id)));
  }, [docs]);

  function toggleSelect(id: string) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function askAboutSelected() {
    router.push(`/chat/new?docs=${selectedIds.join(",")}`);
  }

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  function handleUploadComplete(doc: UploadedDocument) {
    commitDocs([
      {
        id: doc.id,
        filename: doc.filename,
        page_count: null,
        status: doc.status,
        error: null,
        created_at: doc.created_at,
        parsed_at: null,
        embedded_at: null,
      },
      ...docs,
    ]);
    startPolling();
  }

  async function handleConfirmDelete() {
    if (!deleteId) return;
    setDeleteError(null);
    setDeleting(true);
    try {
      await deleteDocument(deleteId);
      commitDocs(docs.filter((d) => d.id !== deleteId));
      setDeleteId(null);
    } catch (err) {
      if (err instanceof ApiError && err.code === "CONFLICT") {
        setDeleteError("This document is still being parsed — try again once it finishes.");
      } else if (err instanceof ApiError && err.code === "STORAGE_ERROR") {
        setDeleteError("Couldn't delete this document right now. It's safe to try again.");
      } else if (err instanceof ApiError) {
        setDeleteError(err.message);
      } else {
        setDeleteError("Something went wrong. Try again.");
      }
    } finally {
      setDeleting(false);
    }
  }

  async function handleRetryLoad() {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await listDocuments();
      commitDocs(result.documents);
      if (result.documents.some((d) => NON_TERMINAL_STATUSES.includes(d.status))) {
        startPolling();
      }
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load your documents.");
    } finally {
      setLoading(false);
    }
  }

  const cardDocs = docs.map(toCardData);

  const librarySection = (
    <>
      <div className="px-[22px] pb-2 pt-6 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
        Library
      </div>
      {cardDocs.length === 0 ? (
        <div className="px-[22px] text-[13px] leading-relaxed text-faint">
          No documents yet.
        </div>
      ) : (
        <div className="flex flex-col gap-px px-3">
          {cardDocs.map((doc) => (
            <div
              key={doc.id}
              className="cursor-pointer truncate rounded-md px-2.5 py-1.5 text-[12.5px] text-muted hover:bg-panel-hover hover:text-ink"
            >
              {doc.filename}
            </div>
          ))}
        </div>
      )}
    </>
  );

  return (
    <div className="grid min-h-screen grid-cols-1 bg-bg text-ink md:grid-cols-[248px_1fr]">
      <Sidebar
        librarySection={librarySection}
        user={USER}
        mobileOpen={mobileMenuOpen}
        onMobileClose={() => setMobileMenuOpen(false)}
        onSignOut={handleSignOut}
      />
      <div className="flex min-w-0 flex-col">
        <Topbar
          left={
            <>
              <MobileMenuButton onClick={() => setMobileMenuOpen(true)} />
              <span className="truncate text-sm font-semibold">Acme Legal</span>
              <WorkspaceBadge>WORKSPACE</WorkspaceBadge>
            </>
          }
          right={
            <>
              <ThemeToggle />
              <div className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full bg-accent text-xs font-semibold text-on-accent">
                {USER.initials}
              </div>
            </>
          }
        />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[760px] animate-fade-up px-6 py-10">
            <div className="mb-8 flex items-baseline justify-between">
              <h1 className="m-0 font-serif text-[28px] font-medium">
                Documents
                <sup className="text-sm font-normal text-accent">1</sup>
              </h1>
              <span className="font-mono text-[11px] tracking-[0.08em] text-faint">
                {cardDocs.length} {cardDocs.length === 1 ? "DOCUMENT" : "DOCUMENTS"}
              </span>
            </div>

            <UploadZone onUploadComplete={handleUploadComplete} />

            {loading ? (
              <div className="mt-10">
                <div className="overflow-hidden rounded-lg border border-line bg-drop-bg">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="flex items-center gap-4 border-b border-line px-[18px] py-3.5 last:border-b-0"
                    >
                      <div className="h-9 w-7 flex-shrink-0 rounded-[3px] bg-panel-active" />
                      <div className="min-w-0 flex-1">
                        <div className="h-3.5 w-2/3 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                        <div className="mt-2 h-2.5 w-1/3 animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : loadError ? (
              <div className="mt-14 text-center">
                <p className="m-0 font-serif text-lg text-muted">Couldn't load your documents</p>
                <p className="mx-auto mt-1 max-w-[320px] text-sm leading-relaxed text-faint">
                  {loadError}
                </p>
                <Button type="button" variant="outline" size="sm" className="mt-4" onClick={handleRetryLoad}>
                  Try again
                </Button>
              </div>
            ) : cardDocs.length === 0 ? (
              <div className="mt-14 text-center">
                <p className="m-0 font-serif text-lg text-muted">No documents yet.</p>
                <p className="mx-auto mt-1 max-w-[320px] text-sm leading-relaxed text-faint">
                  Your library starts with the first PDF you drop above. Every
                  answer will cite its source page.
                </p>
              </div>
            ) : (
              <div className="mt-10">
                <h2 className="m-0 mb-1.5 px-[18px] text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
                  Library
                </h2>
                <div className="overflow-hidden rounded-lg border border-line bg-drop-bg">
                  {cardDocs.map((doc) => (
                    <DocumentCard
                      key={doc.id}
                      doc={doc}
                      onDelete={setDeleteId}
                      selected={selectedIds.includes(doc.id)}
                      onToggleSelect={toggleSelect}
                    />
                  ))}
                </div>
                <p className="m-0 mt-4 px-[18px] font-mono text-[11px] tracking-[0.04em] text-faint">
                  <span className="text-accent">1</span>
                  &nbsp; Parsing usually takes under a minute. Ask questions
                  once a document is ready.
                </p>
              </div>
            )}
          </div>
        </main>
      </div>

      <DeleteConfirmDialog
        filename={deleteTarget?.filename ?? null}
        error={deleteError}
        deleting={deleting}
        onConfirm={handleConfirmDelete}
        onCancel={() => {
          setDeleteId(null);
          setDeleteError(null);
        }}
      />

      {selectedIds.length > 0 ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-6 z-40 flex justify-center">
          <div className="pointer-events-auto flex items-center gap-4 rounded-full border border-line bg-panel py-2 pl-5 pr-2 shadow-[0_12px_40px_rgba(25,23,20,0.16)]">
            <span className="font-mono text-[11px] tracking-[0.08em] text-muted">
              {selectedIds.length} SELECTED
            </span>
            <Button type="button" variant="ghost" size="sm" onClick={() => setSelectedIds([])}>
              Clear
            </Button>
            <Button type="button" size="sm" className="rounded-full" onClick={askAboutSelected}>
              Ask about these
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
