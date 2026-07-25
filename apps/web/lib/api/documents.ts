import { createClient } from "@/lib/supabase/browser";
import type { DocumentStatus } from "@/lib/status-styles";
import { apiFetch, ApiError, forceReauth } from "@/lib/api/client";

export { ApiError };

export interface ApiDocument {
  id: string;
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

export async function listDocuments(): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>("/documents");
}

export async function getDocument(id: string): Promise<ApiDocument> {
  return apiFetch<ApiDocument>(`/documents/${id}`);
}

export async function deleteDocument(id: string): Promise<void> {
  await apiFetch<void>(`/documents/${id}`, { method: "DELETE" });
}

/** Direct-to-Storage upload (ARCHITECTURE.md's ingest flow), then
 * POST /ingest with the resulting storage_path. The Storage SDK's
 * `upload()` is fetch-based internally (confirmed via its source — no
 * XMLHttpRequest, no `onUploadProgress` option anywhere in its type
 * signature), so there is no real byte-level progress to report; callers
 * should show an indeterminate "uploading" state for the duration of
 * this promise rather than a fabricated percentage. */
export async function uploadDocument(file: File): Promise<IngestResponse> {
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
      storage_path: storagePath,
      filename: file.name,
      mime_type: file.type || "application/pdf",
      size_bytes: file.size,
    }),
  });
}
