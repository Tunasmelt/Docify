"use client";

import * as React from "react";
import { ApiError } from "@/lib/api/client";
import { listWorkspaces, type Workspace } from "@/lib/api/workspaces";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

const Context = React.createContext<{
  workspaces: Workspace[];
  active: Workspace;
  select: (id: string) => void;
  refresh: () => Promise<void>;
} | null>(null);

export function useWorkspace() {
  const value = React.useContext(Context);
  if (!value) throw new ApiError(500, "WORKSPACE_UNAVAILABLE", "Workspace is unavailable.");
  return value;
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [workspaces, setWorkspaces] = React.useState<Workspace[]>([]);
  const [activeId, setActiveId] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const refresh = React.useCallback(async () => {
    const result = await listWorkspaces();
    setWorkspaces(result.workspaces);
    setError(null);
  }, []);
  React.useEffect(() => {
    let cancelled = false;
    listWorkspaces().then((result) => {
      if (cancelled) return;
      setWorkspaces(result.workspaces);
      // Session storage is scoped to this tab; validate persisted IDs against
      // the authenticated user's list before using them.
      setActiveId(sessionStorage.getItem("docify-workspace") ?? "");
    }).catch((err) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't load workspaces.");
    });
    return () => { cancelled = true; };
  }, []);
  const select = React.useCallback((id: string) => {
    sessionStorage.setItem("docify-workspace", id);
    setActiveId(id);
  }, []);
  const active = workspaces.find((w) => w.id === activeId) ?? workspaces[0];
  if (!active) return <div className="flex min-h-dvh items-center justify-center bg-bg px-6">
    <div className="max-w-sm text-center">
      <p className="mb-6 font-serif text-[24px] font-semibold">Docify<sup className="ml-0.5 text-xs text-accent">1</sup></p>
      {error ? <><p role="alert" className="text-sm leading-relaxed text-destructive">{error}</p><Button variant="outline" className="mt-4" onClick={() => refresh().catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't load workspaces."))}>Try again</Button></> : <p role="status" className="flex items-center justify-center gap-2 text-sm text-muted"><Loader2 size={16} className="animate-spin" aria-hidden="true" />Loading workspace…</p>}
    </div>
  </div>;
  return <Context.Provider value={{ workspaces, active, select, refresh }}>
    <React.Fragment key={active.id}>{children}</React.Fragment>
  </Context.Provider>;
}
