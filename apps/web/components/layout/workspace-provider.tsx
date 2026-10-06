"use client";

import * as React from "react";
import { ApiError } from "@/lib/api/client";
import { listWorkspaces, type Workspace } from "@/lib/api/workspaces";

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
  if (!active) return <div className="p-6" role="status">{error ?? "Loading workspace…"}{error && <button className="ml-3 underline" onClick={() => refresh().catch(() => {})}>Try again</button>}</div>;
  return <Context.Provider value={{ workspaces, active, select, refresh }}>
    <React.Fragment key={active.id}>{children}</React.Fragment>
  </Context.Provider>;
}
