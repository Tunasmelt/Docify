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

/** POST /query — real retrieve -> generate -> verify round trip
 * (API_CONTRACT.md). `conversationId` omitted starts a new conversation;
 * the real one the backend created comes back on `AskResult.conversationId`
 * either way, so the caller never has to guess which case it was. */
export async function askQuestion(
  question: string,
  documentIds: string[],
  conversationId: string | null
): Promise<AskResult> {
  const res = await apiFetch<QueryApiResponse>("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      document_ids: documentIds,
      conversation_id: conversationId ?? undefined,
    }),
  });

  return {
    conversationId: res.conversation_id,
    userMessage: { id: `${res.message_id}-q`, role: "user", text: question },
    assistantMessage: buildAssistantMessage(res.message_id, res.answer, res.citations),
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
 * still safely assume "no partial content exists yet" when it catches. */
export async function askQuestionStream(
  question: string,
  documentIds: string[],
  conversationId: string | null,
  handlers: QueryStreamHandlers
): Promise<void> {
  const token = await getAccessToken();
  const res = await fetch(`${API_URL}/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      question,
      document_ids: documentIds,
      conversation_id: conversationId ?? undefined,
    }),
  });

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
  } catch {
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
