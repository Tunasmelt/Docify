/**
 * Tests for Settings batch 3, part 1: export conversations
 * (GET /export/conversations, components/settings/export-section.tsx).
 * Real infrastructure discipline — real local Supabase, a real running
 * backend (this is the first Settings feature that's a FastAPI route,
 * not a pure Supabase Auth/Storage call), and a real browser download
 * intercepted via Playwright's download event, its actual saved file
 * content inspected — not just "a network request fired."
 */
import { test, expect, type Page } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedDocument, seedTextChunk, seedConversationTurn } from "./_seed";

const PASSWORD = "test-password-123";

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

async function loginAsNewUser(page: Page, emailPrefix: string): Promise<{ email: string; userId: string }> {
  const email = uniqueEmail(emailPrefix);
  const userId = await createTestUser(email, PASSWORD);
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/documents");
  return { email, userId };
}

test.describe("Settings batch 3: export conversations", () => {
  test("JSON export: real download contains the real seeded conversation, message, and citation", async ({
    page,
  }) => {
    test.setTimeout(30000);
    const { email, userId } = await loginAsNewUser(page, "e2e-export-json");
    try {
      const documentId = await seedDocument(userId, "export-test.pdf");
      const chunkId = await seedTextChunk(userId, documentId, "The export test constant is 42.");
      await seedConversationTurn({
        userId,
        documentIds: [documentId],
        question: "What is the export test constant?",
        answerContent: "The constant is 42 [1].",
        citations: [
          {
            chunk_id: chunkId,
            marker: 1,
            claim_span: "The constant is 42",
            verdict: "supported",
            supporting_quote: "The export test constant is 42.",
          },
        ],
      });

      await page.goto("/settings");
      await expect(page.getByRole("heading", { name: "Export your data" })).toBeVisible();

      const downloadPromise = page.waitForEvent("download");
      await page.getByRole("button", { name: "Export data" }).click();
      const download = await downloadPromise;

      expect(download.suggestedFilename()).toMatch(/^docify-export-.*\.json$/);
      const stream = await download.createReadStream();
      const chunks: Buffer[] = [];
      for await (const chunk of stream!) chunks.push(chunk as Buffer);
      const content = JSON.parse(Buffer.concat(chunks).toString("utf-8"));

      expect(content.conversation_count).toBe(1);
      expect(content.message_count).toBe(2);
      expect(content.citation_count).toBe(1);
      const messages = content.conversations[0].messages;
      expect(messages.some((m: { content: string }) => m.content.includes("What is the export test constant?"))).toBe(
        true
      );
      const assistantMessage = messages.find((m: { role: string }) => m.role === "assistant");
      expect(assistantMessage.citations[0].verdict).toBe("supported");
      expect(assistantMessage.citations[0].supporting_quote).toBe("The export test constant is 42.");
      expect(content.conversations[0].document_names).toContain("export-test.pdf");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("Markdown export: real download renders the conversation with a footnote-style Sources block", async ({
    page,
  }) => {
    test.setTimeout(30000);
    const { email, userId } = await loginAsNewUser(page, "e2e-export-md");
    try {
      const documentId = await seedDocument(userId, "markdown-export.pdf");
      const chunkId = await seedTextChunk(userId, documentId, "Markdown export source content.");
      await seedConversationTurn({
        userId,
        documentIds: [documentId],
        question: "Markdown export question?",
        answerContent: "Markdown export answer [1].",
        citations: [
          {
            chunk_id: chunkId,
            marker: 1,
            claim_span: "Markdown export answer",
            verdict: "supported",
            supporting_quote: "Markdown export source content.",
          },
        ],
      });

      await page.goto("/settings");
      await page.getByRole("radio", { name: /Markdown/ }).click();

      const downloadPromise = page.waitForEvent("download");
      await page.getByRole("button", { name: "Export data" }).click();
      const download = await downloadPromise;

      expect(download.suggestedFilename()).toMatch(/^docify-export-.*\.md$/);
      const stream = await download.createReadStream();
      const chunks: Buffer[] = [];
      for await (const chunk of stream!) chunks.push(chunk as Buffer);
      const text = Buffer.concat(chunks).toString("utf-8");

      expect(text).toContain("# Docify Conversation Export");
      expect(text).toContain("markdown-export.pdf");
      expect(text).toContain("Markdown export answer");
      expect(text).toContain("**Sources**");
      expect(text).toContain("**supported**");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("export is scoped to the logged-in user: a second real user's export never contains the first user's data", async ({
    page,
  }) => {
    test.setTimeout(30000);
    const { email: emailA, userId: userIdA } = await loginAsNewUser(page, "e2e-export-scope-a");
    let emailB: string | undefined;
    try {
      const documentId = await seedDocument(userIdA, "user-a-secret.pdf");
      const chunkId = await seedTextChunk(userIdA, documentId, "User A's private content.");
      await seedConversationTurn({
        userId: userIdA,
        documentIds: [documentId],
        question: "A question only user A asked",
        answerContent: "An answer only user A should see [1].",
        citations: [
          { chunk_id: chunkId, marker: 1, claim_span: "answer", verdict: "supported", supporting_quote: "User A's private content." },
        ],
      });

      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");
      const userB = await loginAsNewUser(page, "e2e-export-scope-b");
      emailB = userB.email;

      await page.goto("/settings");
      const downloadPromise = page.waitForEvent("download");
      await page.getByRole("button", { name: "Export data" }).click();
      const download = await downloadPromise;
      const stream = await download.createReadStream();
      const chunks: Buffer[] = [];
      for await (const chunk of stream!) chunks.push(chunk as Buffer);
      const content = JSON.parse(Buffer.concat(chunks).toString("utf-8"));

      expect(content.conversation_count).toBe(0);
      const raw = JSON.stringify(content);
      expect(raw).not.toContain("user-a-secret.pdf");
      expect(raw).not.toContain("An answer only user A should see");
    } finally {
      await deleteTestUserByEmail(emailA);
      if (emailB) await deleteTestUserByEmail(emailB);
    }
  });
});
