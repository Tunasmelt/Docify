import { API_URL, ApiError, forceReauth, getAccessToken } from "@/lib/api/client";

export type ExportFormat = "json" | "markdown";

/** GET /export/conversations?format=... — deliberately NOT built on
 * apiFetch() (lib/api/client.ts): that helper always calls res.json(),
 * but this endpoint returns a raw file body (application/json or
 * text/markdown) with a Content-Disposition: attachment header, meant
 * to be saved as a file, not parsed as a JS object. Reads the response
 * as a Blob and triggers a real browser download via a temporary
 * object URL + <a click> — the standard, dependency-free pattern for
 * "save this fetch() response as a file" in a browser; no server-side
 * redirect or full-page navigation involved, so the user never leaves
 * Settings.
 *
 * Mirrors apiFetch()'s own 401/error-envelope handling rather than
 * silently diverging, since this is still an authenticated API call
 * that can fail the same ways any other one can. */
export async function downloadConversationExport(format: ExportFormat): Promise<void> {
  const token = await getAccessToken();
  const res = await fetch(`${API_URL}/export/conversations?format=${format}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (res.status === 401) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const code = body?.error?.code ?? "UNKNOWN_ERROR";
    const message = body?.error?.message ?? `Export failed with status ${res.status}`;
    throw new ApiError(res.status, code, message);
  }

  const blob = await res.blob();

  // Real filename from the server's Content-Disposition (routes/export.py
  // stamps a real timestamp into it) rather than inventing one client-side
  // — keeps the saved file's name in sync with whatever the backend
  // actually decided, with a sane fallback if that header is ever
  // missing/malformed for some reason.
  const disposition = res.headers.get("content-disposition") ?? "";
  const filenameMatch = disposition.match(/filename="([^"]+)"/);
  const filename = filenameMatch?.[1] ?? `docify-export.${format === "markdown" ? "md" : "json"}`;

  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    URL.revokeObjectURL(url);
  }
}
