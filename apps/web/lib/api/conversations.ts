import { apiFetch } from "@/lib/api/client";
import type { ApiCitation } from "@/lib/api/types";
import { buildAssistantMessage } from "@/lib/chat/parse-message";
import type { ChatMessage } from "@/lib/types/chat";

export interface ApiConversation {
  id: string;
  title: string | null;
  document_ids: string[];
  message_count: number;
  updated_at: string;
}

export interface ConversationListResponse {
  conversations: ApiConversation[];
  next_cursor: string | null;
}

export async function listConversations(): Promise<ConversationListResponse> {
  return apiFetch<ConversationListResponse>("/conversations");
}

export interface ApiConversationDetail {
  id: string;
  title: string | null;
  document_ids: string[];
  created_at: string;
  updated_at: string;
}

/** POST /conversations/{id}/rename (batch 3) — POST, not PATCH, matching
 * this API's existing action-route precedent (API_CONTRACT.md). Backend
 * trims and validates non-empty/<=200 chars; the trimmed, persisted
 * title comes back in the response rather than echoing what was sent,
 * so the caller never has to duplicate that trimming logic itself. */
export async function renameConversation(conversationId: string, title: string): Promise<ApiConversationDetail> {
  return apiFetch<ApiConversationDetail>(`/conversations/${conversationId}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

/** DELETE /conversations/{id} (batch 3) — 204 on success, apiFetch()
 * already resolves a 204 to undefined (lib/api/client.ts). Messages and
 * citations cascade server-side (SCHEMA.md's on-delete-cascade FKs); no
 * client-side cleanup of anything beyond removing this id from local
 * list state is needed. */
export async function deleteConversation(conversationId: string): Promise<void> {
  await apiFetch<void>(`/conversations/${conversationId}`, { method: "DELETE" });
}

interface ApiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations?: ApiCitation[];
}

interface ConversationMessagesApiResponse {
  conversation: ApiConversationDetail;
  messages: ApiMessage[];
}

export interface ConversationMessages {
  conversation: ApiConversationDetail;
  messages: ChatMessage[];
}

/** GET /conversations/{id}/messages — full history including citations,
 * with each assistant message's `[N]` markers resolved through the same
 * parser POST /query's own live response uses (lib/chat/parse-message.ts),
 * so a reloaded conversation renders byte-identical marker numbers to
 * what the user originally saw (the real-world case FEAT-026's
 * marker-persistence fix exists for). */
export async function getConversationMessages(conversationId: string): Promise<ConversationMessages> {
  const res = await apiFetch<ConversationMessagesApiResponse>(`/conversations/${conversationId}/messages`);

  const messages: ChatMessage[] = res.messages.map((m) =>
    m.role === "user"
      ? { id: m.id, role: "user" as const, text: m.content, createdAt: m.created_at }
      : buildAssistantMessage(m.id, m.content, m.citations ?? [], m.created_at)
  );

  return { conversation: res.conversation, messages };
}
