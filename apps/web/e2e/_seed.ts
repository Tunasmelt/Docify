// Direct REST seeding against the real local Supabase stack (PostgREST +
// Storage), using the same fixed local-dev service-role key as
// _local-supabase.ts. Used only for the parts of chat.e2e.ts that need a
// deterministic figure citation or a deterministic multi-message
// conversation WITHOUT depending on what a real Gemini call happens to
// generate/cite on a given run — the one true end-to-end test (a real
// question through the real UI) still exercises the live
// retrieve -> generate -> verify pipeline for real; this just keeps the
// UI/wiring-correctness assertions (does a stored marker render right,
// does a real figure_url actually load, does history reload correctly)
// deterministic. Mirrors apps/api/tests/test_conversations.py's own
// direct-insert + create_query_turn RPC seeding pattern on the Python
// side — same RPC, same reasoning: seeding through the real persistence
// function keeps seeded data shaped exactly like production writes,
// never hand-crafted rows that could silently drift from the real schema.
import { LOCAL_SUPABASE_SERVICE_ROLE_KEY, LOCAL_SUPABASE_URL } from "./_local-supabase";

const HEADERS = {
  apikey: LOCAL_SUPABASE_SERVICE_ROLE_KEY,
  Authorization: `Bearer ${LOCAL_SUPABASE_SERVICE_ROLE_KEY}`,
  "Content-Type": "application/json",
};

async function restInsert<T>(table: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${LOCAL_SUPABASE_URL}/rest/v1/${table}`, {
    method: "POST",
    headers: { ...HEADERS, Prefer: "return=representation" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`restInsert(${table}) failed: ${res.status} ${await res.text()}`);
  }
  const rows = (await res.json()) as T[];
  return rows[0];
}

const DUMMY_EMBEDDING = new Array(1024).fill(0);

export async function seedDocument(userId: string, filename: string): Promise<string> {
  const doc = await restInsert<{ id: string }>("documents", {
    user_id: userId,
    filename,
    storage_path: `uploads/${userId}/${filename}`,
    mime_type: "application/pdf",
    size_bytes: 1,
    status: "ready",
  });
  return doc.id;
}

export async function seedTextChunk(
  userId: string,
  documentId: string,
  content: string,
  pageNumber = 1,
  chunkIndex = 0
): Promise<string> {
  const chunk = await restInsert<{ id: string }>("chunks", {
    document_id: documentId,
    user_id: userId,
    chunk_index: chunkIndex,
    element_type: "text",
    page_number: pageNumber,
    content,
    embedding: DUMMY_EMBEDDING,
  });
  return chunk.id;
}

/** Uploads real PNG bytes to the real (private) `figures` bucket and
 * inserts a matching figure-type chunk row — the exact combination
 * routes/query.py's figure_url mechanism (a real signed URL against a
 * real Storage object) needs to be genuinely, not just structurally,
 * correct. */
export async function seedFigureChunk(
  userId: string,
  documentId: string,
  pngBytes: Buffer,
  pageNumber = 1,
  chunkIndex = 1
): Promise<{ chunkId: string; figurePath: string }> {
  const figurePath = `${userId}/${documentId}/0.png`;
  const uploadRes = await fetch(`${LOCAL_SUPABASE_URL}/storage/v1/object/figures/${figurePath}`, {
    method: "POST",
    headers: {
      apikey: LOCAL_SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${LOCAL_SUPABASE_SERVICE_ROLE_KEY}`,
      "Content-Type": "image/png",
    },
    // Buffer vs DOM BodyInit generic mismatch under this TS/@types/node
    // combination — a known typing friction point, not a real type error
    // (Node's fetch accepts a Buffer as a request body at runtime).
    body: pngBytes as unknown as BodyInit,
  });
  if (!uploadRes.ok) {
    throw new Error(`figure upload failed: ${uploadRes.status} ${await uploadRes.text()}`);
  }

  const chunk = await restInsert<{ id: string }>("chunks", {
    document_id: documentId,
    user_id: userId,
    chunk_index: chunkIndex,
    element_type: "figure",
    page_number: pageNumber,
    content: "",
    figure_path: figurePath,
    embedding: DUMMY_EMBEDDING,
  });
  return { chunkId: chunk.id, figurePath };
}

export interface SeedCitation {
  chunk_id: string;
  marker: number;
  claim_span: string;
  verdict: "supported" | "partial" | "unsupported";
  supporting_quote: string | null;
  verifier_model?: string;
}

/** Calls the real create_query_turn RPC (migrations/20260724_002 +
 * 20260725_002) — the same function POST /query itself calls — so a
 * seeded conversation/message/citation set is byte-shape-identical to
 * one a real request would have produced. */
export async function seedConversationTurn(params: {
  userId: string;
  conversationId?: string | null;
  documentIds: string[];
  question: string;
  answerContent: string;
  citations: SeedCitation[];
}): Promise<{ conversationId: string; messageId: string }> {
  const res = await fetch(`${LOCAL_SUPABASE_URL}/rest/v1/rpc/create_query_turn`, {
    method: "POST",
    headers: HEADERS,
    body: JSON.stringify({
      p_user_id: params.userId,
      p_conversation_id: params.conversationId ?? null,
      p_document_ids: params.documentIds,
      p_question: params.question,
      p_answer_content: params.answerContent,
      p_answer_raw_content: params.answerContent,
      p_retrieved_chunk_ids: params.citations.map((c) => c.chunk_id),
      p_answer_metadata: { model: "seeded", input_tokens: 0, output_tokens: 0, latency_ms: 0 },
      p_citations: params.citations.map((c) => ({ verifier_model: "seeded", ...c })),
    }),
  });
  if (!res.ok) {
    throw new Error(`create_query_turn RPC failed: ${res.status} ${await res.text()}`);
  }
  const rows = (await res.json()) as { conversation_id: string; message_id: string }[];
  return { conversationId: rows[0].conversation_id, messageId: rows[0].message_id };
}

// Minimal, valid 1x1 red PNG — small enough to inline, real enough to
// decode and render (not a placeholder string masquerading as an image).
export const TINY_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg==";

export function decodeBase64Png(): Buffer {
  return Buffer.from(TINY_PNG_BASE64, "base64");
}
