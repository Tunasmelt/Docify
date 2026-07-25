import { createClient } from "@/lib/supabase/browser";

/** Thrown by every function using apiFetch() on a non-2xx response.
 * Carries the backend's real error envelope (API_CONTRACT.md: `{error:
 * {code, message}}`) rather than a generic "request failed" — callers
 * need the real `code` to tell a 409 (parsing in progress) apart from a
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

export const API_URL = process.env.NEXT_PUBLIC_API_URL!;

/** No backend route requires no active session, so every caller needs a
 * token. If the session itself is gone (refresh token also expired —
 * not just a near-expiry access token, which the client refreshes
 * transparently), there is nothing to send: force the same sign-out +
 * redirect a stale-token 401 gets below, rather than firing a request
 * with no Authorization header and waiting for the 401 round-trip to
 * tell us what we already know. */
export async function getAccessToken(): Promise<string> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  if (!data.session) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }
  return data.session.access_token;
}

export async function forceReauth(): Promise<void> {
  const supabase = createClient();
  await supabase.auth.signOut();
  // Full navigation, not router.push — guarantees middleware re-evaluates
  // from a clean slate and any stale client-side session state is gone,
  // not just the visible route.
  window.location.href = "/login";
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
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
