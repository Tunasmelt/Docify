import { API_URL, ApiError, apiFetch, forceReauth, getAccessToken } from "@/lib/api/client";
import type { ApiCitation } from "@/lib/api/types";
import { buildAssistantMessage } from "@/lib/chat/parse-message";
import type { AssistantMessage, UserMessage } from "@/lib/types/chat";

export interface QueryMetadata {
  model: string;
  verifier_model: string;
  retrieved_count: number;
  cited_count: number;
  latency_ms: number;
}

interface QueryApiResponse {
  conversation_id: string;
  message_id: string;
  answer: string;
  citations: ApiCitation[];
  metadata: QueryMetadata;
}

export interface AskResult {
  conversationId: string;
  userMessage: UserMessage;
  assistantMessage: AssistantMessage;
  metadata: QueryMetadata;
}

/** Settings batch 2 — QueryRequest.k/rerank (apps/api/models/query.py)
 * are already real, independently-optional request parameters; this is
 * just the client-side shape for passing a caller's chosen values
 * through. Omitting either lets the backend's own defaults apply
 * (k=8, rerank=false) — a caller passing nothing gets byte-identical
 * behavior to before this option existed. */
export interface QueryOptions {
  k?: number;
  rerank?: boolean;
}

/** POST /query — real retrieve -> generate -> verify round trip
 * (API_CONTRACT.md). `conversationId` omitted starts a new conversation;
 * the real one the backend created comes back on `AskResult.conversationId`
 * either way, so the caller never has to guess which case it was. */
export async function askQuestion(
  question: string,
  documentIds: string[],
  conversationId: string | null,
  options?: QueryOptions
): Promise<AskResult> {
  const res = await apiFetch<QueryApiResponse>("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      document_ids: documentIds,
      conversation_id: conversationId ?? undefined,
      k: options?.k,
      rerank: options?.rerank,
    }),
  });

  // POST /query's response has no per-message created_at (API_CONTRACT.md)
  // — "now" is the honest approximation for a request that just
  // round-tripped synchronously, same as the streaming path's
  // stream-started timestamp below.
  const now = new Date().toISOString();
  return {
    conversationId: res.conversation_id,
    userMessage: { id: `${res.message_id}-q`, role: "user", text: question, createdAt: now },
    assistantMessage: buildAssistantMessage(res.message_id, res.answer, res.citations, now),
    metadata: res.metadata,
  };
}

interface CitationsResolvedEvent {
  conversation_id: string;
  message_id: string;
  answer: string;
  citations: ApiCitation[];
}

/** Callbacks for POST /query/stream's SSE event sequence (FEAT-016,
 * 2026-07-27, API_CONTRACT.md): `retrieving -> token* -> verifying ->
 * citations-resolved -> done`, with `error` able to replace any step
 * from `token` onward. Citations (and therefore verdict-based styling)
 * are only ever delivered via onCitationsResolved, once verification
 * has actually run — onToken's raw text may contain unresolved `[N]`
 * brackets that must render as plain inert text, never as a styled or
 * clickable citation, until that point. */
export interface QueryStreamHandlers {
  onRetrieving?: () => void;
  onToken: (text: string) => void;
  onVerifying?: () => void;
  onCitationsResolved: (event: CitationsResolvedEvent) => void;
  onDone?: (metadata: QueryMetadata) => void;
  onError: (message: string) => void;
}

/** POST /query/stream — SSE variant of askQuestion(). Browsers'
 * EventSource can't send a POST body or an Authorization header, so this
 * uses fetch() with a manually-read ReadableStream instead — the same
 * bearer-token auth as apiFetch(), just without its single
 * res.json()-then-return shape, since a streaming body has to be
 * consumed incrementally. Resolves once the stream ends (whether via a
 * real `done`/`error` event or the connection just closing); never
 * throws for anything that happened AFTER the connection was
 * established — all failure signaling past that point goes through
 * handlers.onError, since by then the caller may already be showing
 * partial streamed content it needs to keep visible, not discard via a
 * thrown exception. Only throws for a failure BEFORE any streaming
 * began (a non-2xx response, a network error opening the connection) —
 * mirroring apiFetch()'s contract for that case, so the caller can
 * still safely assume "no partial content exists yet" when it catches.
 *
 * `signal` (batch 2, stop-generation): an aborted signal closes the
 * underlying fetch's TCP connection — investigated and confirmed
 * (2026-08-04) to hit the EXACT SAME server-side path already proven
 * for an accidental disconnect (routes/query.py's `_watch_for_disconnect`
 * reacts to the ASGI transport's `http.disconnect`, a low-level signal
 * that fires on ANY connection loss regardless of cause — this project's
 * uvicorn server speaks plain HTTP/1.1 in local dev with no reverse-
 * proxy connection pooling in front of it, so `AbortController.abort()`
 * on this fetch closes the same one-request-per-connection socket a
 * real tab-close or `response.close()` would). A caller-initiated abort
 * therefore surfaces here as an AbortError, deliberately swallowed
 * (below) rather than routed through handlers.onError — the backend has
 * already discarded the turn via its own proven mechanism; there is
 * nothing left to report as a failure. */
export async function askQuestionStream(
  question: string,
  documentIds: string[],
  conversationId: string | null,
  handlers: QueryStreamHandlers,
  signal?: AbortSignal,
  options?: QueryOptions
): Promise<void> {
  const token = await getAccessToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        question,
        document_ids: documentIds,
        conversation_id: conversationId ?? undefined,
        k: options?.k,
        rerank: options?.rerank,
      }),
      signal,
    });
  } catch (err) {
    // Aborted before the connection even finished opening (a very fast
    // stop click, or a stop during the retrieval-not-yet-visible
    // window) — same "nothing to report" reasoning as the mid-stream
    // case below, just at an earlier point in the fetch's lifecycle.
    if (err instanceof DOMException && err.name === "AbortError") return;
    throw err;
  }

  if (res.status === 401) {
    await forceReauth();
    throw new ApiError(401, "UNAUTHORIZED", "Session expired");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const code = body?.error?.code ?? "UNKNOWN_ERROR";
    const message = body?.error?.message ?? `Request failed with status ${res.status}`;
    throw new ApiError(res.status, code, message);
  }

  if (!res.body) {
    throw new ApiError(res.status, "STREAM_UNSUPPORTED", "This browser did not return a readable stream body");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let frameEnd = buffer.indexOf("\n\n");
      while (frameEnd !== -1) {
        dispatchSseFrame(buffer.slice(0, frameEnd), handlers);
        buffer = buffer.slice(frameEnd + 2);
        frameEnd = buffer.indexOf("\n\n");
      }
    }
  } catch (err) {
    // A caller-initiated stop (batch 2) — reader.read() rejects with
    // AbortError the same way it would for any other severed
    // connection, but this one was deliberate: the backend's own
    // disconnect mechanism has already discarded the turn (see this
    // function's docstring), so there is nothing to surface as a
    // failure. Silently return, exactly like the pre-body-read abort
    // case above.
    if (err instanceof DOMException && err.name === "AbortError") return;
    // A real mid-stream transport failure (connection reset, server
    // process killed) — the SDK/fetch layer throws here rather than
    // ever delivering a clean `error` SSE frame, since the connection
    // itself is what broke. Surfaced the same way as a server-sent
    // `error` event so the caller has exactly one failure path to
    // handle, not two.
    handlers.onError("Connection to the server was lost while streaming the answer.");
  }
}

function dispatchSseFrame(frame: string, handlers: QueryStreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trim());
  }
  if (dataLines.length === 0) return;

  const data = JSON.parse(dataLines.join("\n"));

  switch (event) {
    case "retrieving":
      handlers.onRetrieving?.();
      break;
    case "token":
      handlers.onToken(data.text as string);
      break;
    case "verifying":
      handlers.onVerifying?.();
      break;
    case "citations-resolved":
      handlers.onCitationsResolved(data as CitationsResolvedEvent);
      break;
    case "done":
      handlers.onDone?.(data.metadata as QueryMetadata);
      break;
    case "error":
      handlers.onError((data.message as string) ?? "Something went wrong answering that question.");
      break;
  }
}
