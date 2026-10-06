import { apiFetch } from "@/lib/api/client";

export interface Workspace {
  id: string;
  name: string;
  created_at: string;
  document_count: number;
}

export function listWorkspaces() {
  return apiFetch<{ workspaces: Workspace[] }>("/workspaces");
}

export function saveWorkspace(name: string, id?: string) {
  return apiFetch<Workspace>(id ? `/workspaces/${id}` : "/workspaces", {
    method: id ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function deleteWorkspace(id: string) {
  return apiFetch<void>(`/workspaces/${id}`, { method: "DELETE" });
}
