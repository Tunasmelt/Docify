import { apiFetch } from "@/lib/api/client";
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
