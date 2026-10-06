"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useWorkspace } from "@/components/layout/workspace-provider";
import { deleteWorkspace, saveWorkspace } from "@/lib/api/workspaces";
import { ApiError } from "@/lib/api/client";

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
  return <div className="px-4 pb-4 text-sm">
    <label htmlFor="workspace" className="mb-1 block text-xs text-muted">Workspace</label>
    <select id="workspace" value={active.id} className="w-full rounded border border-line bg-panel p-2" disabled={busy} onChange={(event) => {
      router.push("/documents");
      select(event.target.value);
    }}>
      {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
    </select>
    <div className="mt-2 flex gap-3 text-xs text-muted">
      {(["create", "rename", "delete"] as const).map((action) => <button key={action} disabled={busy} onClick={() => {
        setMode(action); setName(action === "rename" ? active.name : ""); setError(null);
      }}>{action === "create" ? "New" : action === "rename" ? "Rename" : "Delete"}</button>)}
    </div>
    {mode && <form onSubmit={submit} className="mt-3 space-y-2">
      {mode === "delete" ? <p>Delete “{active.name}” and its conversations? Remove its documents first.</p> : <input aria-label="Workspace name" required maxLength={60} value={name} onChange={(event) => setName(event.target.value)} className="w-full rounded border border-line bg-panel p-2" />}
      {error && <p role="alert">{error}</p>}
      <div className="flex gap-3"><button disabled={busy} type="submit">{busy ? "Saving…" : mode === "delete" ? "Confirm delete" : "Save"}</button><button disabled={busy} type="button" onClick={() => setMode(null)}>Cancel</button></div>
    </form>}
  </div>;
}
