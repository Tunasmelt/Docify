import { createClient } from "@/lib/supabase/browser";
import type { DocumentStatus } from "@/lib/status-styles";

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

/** Thrown by every function below on a non-2xx response. Carries the
 * backend's real error envelope (API_CONTRACT.md: `{error: {code,
 * message}}`) rather than a generic "request failed" — callers need the
 * real `code` to tell a 409 (parsing in progress) apart from a
 * STORAGE_ERROR, both of which need distinct, human copy in the UI. */
export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const API_URL = process.env.NEXT_PUBLIC_API_URL!;

/** No backend route requires no active session, so every caller in this
 * module needs a token. If the session itself is gone (refresh token
 * also expired — not just a near-expiry access token, which the client
 * refreshes transparently), there is nothing to send: force the same
 * sign-out + redirect a stale-token 401 gets below, rather than firing a
 * request with no Authorization header and waiting for the 401
 * round-trip to tell us what we already know. */
async function getAccessToken(): Promise<string> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  if (!data.session) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }
  return data.session.access_token;
}

async function forceReauth(): Promise<void> {
  const supabase = createClient();
  await supabase.auth.signOut();
  // Full navigation, not router.push — guarantees middleware re-evaluates
  // from a clean slate and any stale client-side session state is gone,
  // not just the visible route.
  window.location.href = "/login";
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getAccessToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${token}`,
    },
  });

  if (res.status === 401) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const body = await res.json().catch(() => null);

  if (!res.ok) {
    const code = body?.error?.code ?? "UNKNOWN_ERROR";
    const message = body?.error?.message ?? `Request failed with status ${res.status}`;
    throw new ApiError(res.status, code, message);
  }

  return body as T;
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
