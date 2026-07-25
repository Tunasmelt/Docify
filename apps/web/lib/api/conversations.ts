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

interface ApiConversationDetail {
  id: string;
  title: string | null;
  document_ids: string[];
  created_at: string;
  updated_at: string;
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
      ? { id: m.id, role: "user" as const, text: m.content }
      : buildAssistantMessage(m.id, m.content, m.citations ?? [])
  );

  return { conversation: res.conversation, messages };
}
