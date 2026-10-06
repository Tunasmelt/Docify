import { createClient } from "@/lib/supabase/browser";
import type { DocumentStatus } from "@/lib/status-styles";
import { API_URL, apiFetch, ApiError, forceReauth, getAccessToken } from "@/lib/api/client";
import type { CitationBBox } from "@/lib/api/types";

export { ApiError };

export interface ApiDocument {
  id: string;
  workspace_id: string;
  filename: string;
  page_count: number | null;
  status: DocumentStatus;
  error: string | null;
  created_at: string;
  parsed_at: string | null;
  embedded_at: string | null;
}

export interface DocumentListResponse {
  documents: ApiDocument[];
  next_cursor: string | null;
}

export interface IngestResponse {
  document_id: string;
  status: DocumentStatus;
  created_at: string;
}

export async function listDocuments(workspaceId?: string): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>(workspaceId ? `/documents?workspace_id=${encodeURIComponent(workspaceId)}` : "/documents");
}

export async function getDocument(id: string): Promise<ApiDocument> {
  return apiFetch<ApiDocument>(`/documents/${id}`);
}

/** Changes the document's display name (PATCH /documents/{id}). */
export async function renameDocument(id: string, filename: string): Promise<ApiDocument> {
  return apiFetch<ApiDocument>(`/documents/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
}

export async function deleteDocument(id: string): Promise<void> {
  await apiFetch<void>(`/documents/${id}`, { method: "DELETE" });
}

/** Re-runs parsing + embedding for an existing document (POST /reindex) —
 * the recovery path for a document that ended in `failed`. Resolves once
 * the API has reset it to `parsing`; poll for the outcome. */
export async function reindexDocument(id: string): Promise<IngestResponse> {
  return apiFetch<IngestResponse>(`/reindex/${id}`, { method: "POST" });
}

/** Direct-to-Storage upload (ARCHITECTURE.md's ingest flow), then
 * POST /ingest with the resulting storage_path. The Storage SDK's
 * `upload()` is fetch-based internally (confirmed via its source — no
 * XMLHttpRequest, no `onUploadProgress` option anywhere in its type
 * signature), so there is no real byte-level progress to report; callers
 * should show an indeterminate "uploading" state for the duration of
 * this promise rather than a fabricated percentage. */
export async function uploadDocument(file: File, workspaceId?: string): Promise<IngestResponse> {
  const supabase = createClient();
  const { data: sessionData } = await supabase.auth.getSession();
  if (!sessionData.session) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }
  const userId = sessionData.session.user.id;

  const extension = file.name.includes(".") ? file.name.split(".").pop() : "pdf";
  const objectPath = `${userId}/${crypto.randomUUID()}.${extension}`;

  const { error: uploadError } = await supabase.storage
    .from("uploads")
    .upload(objectPath, file, { contentType: file.type || "application/pdf" });

  if (uploadError) {
    throw new ApiError(uploadError.status ?? 500, "STORAGE_ERROR", uploadError.message);
  }

  // API_CONTRACT.md's storage_path includes the bucket name itself —
  // the Storage SDK call above does not (the bucket is selected via
  // .from("uploads") instead), so it's prefixed back on here.
  const storagePath = `uploads/${objectPath}`;

  return apiFetch<IngestResponse>("/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: workspaceId,
      storage_path: storagePath,
      filename: file.name,
      mime_type: file.type || "application/pdf",
      size_bytes: file.size,
    }),
  });
}

/** Renders one PDF page server-side, with `bbox` highlighted, and returns
 * an object URL for an <img>. The caller must URL.revokeObjectURL() it.
 * (A plain <img src> can't send the bearer token, hence fetch + blob.) */
export async function fetchPageImage(
  documentId: string,
  pageNumber: number,
  bbox?: CitationBBox
): Promise<string> {
  const token = await getAccessToken();
  const params = bbox
    ? "?" + new URLSearchParams({
        x0: String(bbox.x0),
        y0: String(bbox.y0),
        x1: String(bbox.x1),
        y1: String(bbox.y1),
      }).toString()
    : "";
  const res = await fetch(`${API_URL}/documents/${documentId}/pages/${pageNumber}/image${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(
      res.status,
      body?.error?.code ?? "UNKNOWN_ERROR",
      body?.error?.message ?? `Request failed with status ${res.status}`
    );
  }
  return URL.createObjectURL(await res.blob());
}

export interface SourceContextBlock {
  chunk_id: string;
  element_type: string;
  /** Chunk text; tables are markdown. */
  content: string;
  cited: boolean;
  figure_url: string | null;
}

export interface SourceContext {
  kind: "slide" | "section";
  /** "Slide N", the section heading, or null for text outside any section. */
  label: string | null;
  blocks: SourceContextBlock[];
}

/** The cited chunk with its surroundings, for DOCX/PPTX/HTML sources. */
export async function fetchSourceContext(documentId: string, chunkId: string): Promise<SourceContext> {
  return apiFetch<SourceContext>(`/documents/${documentId}/chunks/${chunkId}/context`);
}
