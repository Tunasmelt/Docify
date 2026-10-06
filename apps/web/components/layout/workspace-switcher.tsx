"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useWorkspace } from "@/components/layout/workspace-provider";
import { deleteWorkspace, saveWorkspace } from "@/lib/api/workspaces";
import { ApiError } from "@/lib/api/client";
import { ChevronDown, Layers2, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

export function WorkspaceSwitcher() {
  const { workspaces, active, select, refresh } = useWorkspace();
  const router = useRouter();
  const [mode, setMode] = React.useState<"create" | "rename" | "delete" | null>(null);
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "delete") {
        await deleteWorkspace(active.id);
        await refresh();
        router.push("/documents");
      } else {
        const saved = await saveWorkspace(name, mode === "rename" ? active.id : undefined);
        await refresh();
        select(saved.id);
        if (mode === "create") router.push("/documents");
      }
      setMode(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save workspace.");
    } finally { setBusy(false); }
  }
  return <div className="mx-3 mb-4 border-b border-line pb-4 text-sm">
    <label htmlFor="workspace" className="mb-2 block px-2 font-mono text-[10px] uppercase tracking-widest text-muted">Workspace</label>
    <div className="relative">
      <Layers2 size={16} strokeWidth={1.6} aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-accent" />
    <select id="workspace" value={active.id} className="h-11 w-full appearance-none rounded-md border border-line bg-panel-active pl-9 pr-8 text-[13px] font-medium text-ink transition-colors hover:border-border focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-focus-ring disabled:opacity-50" disabled={busy} onChange={(event) => {
      router.push("/documents");
      select(event.target.value);
    }}>
      {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
    </select>
      <ChevronDown size={14} aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted" />
    </div>
    <div className="mt-1.5 flex items-center gap-1">
      {(["create", "rename", "delete"] as const).map((action) => <Button key={action} type="button" variant="ghost" size="sm" className={`h-9 gap-1.5 px-2 text-[11px] font-medium ${action === "delete" ? "ml-auto hover:bg-destructive-bg hover:text-destructive" : "text-muted"}`} disabled={busy} onClick={() => {
        setMode(action); setName(action === "rename" ? active.name : ""); setError(null);
      }}>{action === "create" ? <Plus size={13} aria-hidden="true" /> : action === "rename" ? <Pencil size={13} aria-hidden="true" /> : <Trash2 size={13} aria-hidden="true" />}{action === "create" ? "New" : action === "rename" ? "Rename" : "Delete"}</Button>)}
    </div>
    <Dialog open={mode !== null} onOpenChange={(open) => { if (!open && !busy) setMode(null); }}>
      <DialogContent className="max-h-[calc(100dvh-48px)] overflow-y-auto">
        <DialogTitle>{mode === "delete" ? "Delete workspace" : mode === "rename" ? "Rename workspace" : "New workspace"}</DialogTitle>
        <DialogDescription className="break-words">{mode === "delete" ? `Delete “${active.name}” and its conversations? Remove its documents first. This cannot be undone.` : mode === "rename" ? "Give this workspace a name that's easy to find." : "Keep a separate library of documents and conversations."}</DialogDescription>
        <form onSubmit={submit}>
          {mode !== "delete" && <div className="space-y-2"><label htmlFor="workspace-name" className="block text-[13px] font-medium">Workspace name</label><Input id="workspace-name" autoFocus required maxLength={60} placeholder="e.g. Research" disabled={busy} value={name} onChange={(event) => setName(event.target.value)} /></div>}
          {error && <p role="alert" className="mt-4 rounded-md bg-destructive-bg px-3 py-2 text-sm text-destructive">{error}</p>}
          <div className="mt-6 flex justify-end gap-2"><Button variant="outline" disabled={busy} type="button" onClick={() => setMode(null)}>Cancel</Button><Button variant={mode === "delete" ? "destructive" : "default"} disabled={busy || (mode !== "delete" && !name.trim())} type="submit">{busy ? mode === "delete" ? "Deleting…" : "Saving…" : mode === "delete" ? "Confirm delete" : "Save"}</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  </div>;
}
