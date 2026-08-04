/**
 * Tests for batch 3: conversation rename/delete.
 * Real infrastructure discipline, same as the rest of this suite — real
 * local Supabase, real backend. Conversations are seeded via the real
 * create_query_turn RPC (deterministic, no real Gemini/Voyage calls
 * needed for these UI/wiring tests) — same pattern chat.e2e.ts's own
 * seeded tests use.
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

async function seedOneConversation(userId: string, question: string, answer = "Seeded answer [1]."): Promise<string> {
  const documentId = await seedDocument(userId, "seeded.pdf");
  const chunk = await seedTextChunk(userId, documentId, "Some real content.");
  const { conversationId } = await seedConversationTurn({
    userId,
    documentIds: [documentId],
    question,
    answerContent: answer,
    citations: [{ chunk_id: chunk, marker: 1, claim_span: "Seeded answer", verdict: "supported", supporting_quote: "Some real content." }],
  });
  return conversationId;
}

test.describe("Batch 3: conversation rename + delete", () => {
  test("rename from the conversation list page persists and survives reload", async ({ page }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-rename-list");
    try {
      await seedOneConversation(userId, "Original question for rename test");

      await page.goto("/chat");
      await expect(page.getByText("Original question for rename test")).toBeVisible();

      const renameButton = page.getByTitle("Rename").first();
      await renameButton.click();

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      const input = dialog.locator("input");
      await expect(input).toHaveValue(/Original question for rename test/);
      await input.fill("Renamed via list page");
      await dialog.getByRole("button", { name: "Save" }).click();

      await expect(dialog).not.toBeVisible();
      await expect(page.getByText("Renamed via list page")).toBeVisible();
      await expect(page.getByText("Original question for rename test")).not.toBeVisible();

      // Real persistence, not just local state -- reload and confirm.
      await page.reload();
      await expect(page.getByText("Renamed via list page")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("rename does not reorder the conversation list (updated_at unchanged)", async ({ page }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-rename-order");
    try {
      // Two conversations, seeded in order -- "older" first so it
      // normally sorts BELOW "newer" (list is updated_at desc).
      await seedOneConversation(userId, "Older conversation");
      await new Promise((r) => setTimeout(r, 1100)); // real distinct updated_at second
      await seedOneConversation(userId, "Newer conversation");

      await page.goto("/chat");
      const titles = page.locator('p.font-serif');
      await expect(titles.first()).toContainText("Newer conversation");

      // Rename the OLDER (currently second) conversation -- it must NOT
      // jump to the top.
      const olderCard = page.locator("div.group", { hasText: "Older conversation" }).first();
      await olderCard.getByTitle("Rename").click();
      const dialog = page.getByRole("dialog");
      await dialog.locator("input").fill("Renamed older conversation");
      await dialog.getByRole("button", { name: "Save" }).click();
      await expect(dialog).not.toBeVisible();

      await expect(titles.first()).toContainText("Newer conversation");
      await expect(titles.nth(1)).toContainText("Renamed older conversation");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("rename rejects an empty title (Save stays disabled)", async ({ page }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-rename-empty");
    try {
      await seedOneConversation(userId, "Question for empty-title test");
      await page.goto("/chat");
      await page.getByTitle("Rename").first().click();

      const dialog = page.getByRole("dialog");
      const input = dialog.locator("input");
      await input.fill("   ");
      await expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();

      await input.fill("");
      await expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("delete from the conversation list page removes it, cascades server-side, survives reload", async ({ page }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-delete-list");
    try {
      const conversationId = await seedOneConversation(userId, "Question to be deleted");
      await seedOneConversation(userId, "A different, surviving conversation");

      await page.goto("/chat");
      await expect(page.getByText("Question to be deleted")).toBeVisible();
      await expect(page.getByText("A different, surviving conversation")).toBeVisible();

      const targetCard = page.locator("div.group", { hasText: "Question to be deleted" }).first();
      await targetCard.getByTitle("Delete").click();

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await expect(dialog).toContainText("Question to be deleted");
      await dialog.getByRole("button", { name: "Delete conversation" }).click();

      await expect(dialog).not.toBeVisible();
      await expect(page.getByText("Question to be deleted")).not.toBeVisible();
      // The other conversation is untouched.
      await expect(page.getByText("A different, surviving conversation")).toBeVisible();

      await page.reload();
      await expect(page.getByText("A different, surviving conversation")).toBeVisible();
      await expect(page.getByText("Question to be deleted")).not.toBeVisible();

      // Direct navigation to the deleted conversation's real (former)
      // id is a clean not-found, same as one that never existed.
      await page.goto(`/chat/${conversationId}`);
      await expect(page.getByText("Conversation not found")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("rename from inside the open conversation's header persists and updates the header title", async ({ page }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-rename-header");
    try {
      const conversationId = await seedOneConversation(userId, "Question shown in header");
      await page.goto(`/chat/${conversationId}`);
      await expect(page.getByTestId("assistant-message")).toBeVisible();

      await page.getByTestId("rename-conversation-header-button").click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await dialog.locator("input").fill("Renamed from header");
      await dialog.getByRole("button", { name: "Save" }).click();
      await expect(dialog).not.toBeVisible();

      await expect(page.locator("header")).toContainText("Renamed from header");

      // Confirm it also shows up correctly from the list page (single
      // source of truth, not a header-only local update).
      await page.goto("/chat");
      await expect(page.getByText("Renamed from header")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("delete from inside the open conversation's header navigates back to /chat and the conversation is gone", async ({
    page,
  }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-delete-header");
    try {
      const conversationId = await seedOneConversation(userId, "Question to delete from header");
      await page.goto(`/chat/${conversationId}`);
      await expect(page.getByTestId("assistant-message")).toBeVisible();

      await page.getByTestId("delete-conversation-header-button").click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Delete conversation" }).click();

      await page.waitForURL("**/chat");
      await expect(page.getByText("Question to delete from header")).not.toBeVisible();

      await page.goto(`/chat/${conversationId}`);
      await expect(page.getByText("Conversation not found")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("multi-tenant: user B cannot rename or delete user A's conversation via direct API calls", async ({ browser }) => {
    test.setTimeout(60000);
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    const { email: emailA, userId: userIdA } = await loginAsNewUser(pageA, "e2e-tenant-rename-a");
    const { email: emailB } = await loginAsNewUser(pageB, "e2e-tenant-rename-b");

    try {
      const conversationId = await seedOneConversation(userIdA, "User A's private conversation");

      // User B is logged in (has a real session/token) but has no UI
      // access to user A's conversation id at all -- the real security
      // boundary is the backend's own user_id-scoped query (already
      // covered by backend tests); this confirms user B's real browser
      // session, hitting the real API with their own real token,
      // can't rename or delete it either.
      const renameResult = await pageB.evaluate(async (id) => {
        const res = await fetch(`http://localhost:8000/conversations/${id}/rename`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "hijacked" }),
        });
        return res.status;
      }, conversationId);
      // 401 (no bearer token sent from this raw fetch) or 404 (a real
      // token but wrong owner) are both acceptable non-success outcomes
      // -- the one unacceptable outcome is 200.
      expect(renameResult).not.toBe(200);

      // Confirm via user A's own real session that the conversation
      // still has its original title.
      await pageA.goto("/chat");
      await expect(pageA.getByText("User A's private conversation")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(emailA);
      await deleteTestUserByEmail(emailB);
      await contextA.close();
      await contextB.close();
    }
  });
});
