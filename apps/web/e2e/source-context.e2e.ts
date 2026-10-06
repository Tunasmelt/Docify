/**
 * "Show in document" for DOCX/PPTX/HTML citations (2026-10-06). PDFs open the
 * rendered page (page-preview.e2e.ts); the other formats have no page image,
 * so the dialog shows the cited slide or section as text with the cited
 * passage highlighted. Before this, these citations had no button at all.
 *
 * Runs against the local Supabase stack and a live local FastAPI backend.
 */
import { test, expect, type Page } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedConversationTurn, seedDocumentWithChunks } from "./_seed";

const PASSWORD = "test-password-123";
const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

async function openCitedConversation(page: Page, email: string, userId: string, documentId: string, chunkId: string) {
  const { conversationId } = await seedConversationTurn({
    userId,
    documentIds: [documentId],
    question: "What happened in Q3?",
    answerContent: "Revenue grew [1].",
    citations: [{ chunk_id: chunkId, marker: 1, claim_span: "Revenue grew", verdict: "supported", supporting_quote: null }],
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/documents");
  await page.goto(`/chat/${conversationId}`);
  await page.locator('[data-verdict="supported"]').first().click();
}

test.describe("Source preview for non-PDF documents", () => {
  test("a PPTX citation opens its whole slide with the cited text highlighted", async ({ page }) => {
    const email = `e2e-slide-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
    const userId = await createTestUser(email, PASSWORD);
    try {
      const { documentId, chunkIds } = await seedDocumentWithChunks(userId, "deck.pptx", PPTX, [
        { content: "Title slide", page: 1 },
        { content: "Q3 2026 Highlights", page: 2, elementType: "heading" },
        { content: "Revenue reached $1,410,000", page: 2 },
        { content: "Thank you", page: 3 },
      ]);
      await openCitedConversation(page, email, userId, documentId, chunkIds[2]);

      await page.getByRole("button", { name: "Open slide 2 in document" }).click();

      const dialog = page.getByRole("dialog");
      await expect(dialog.getByText("deck.pptx · slide 2")).toBeVisible();
      const blocks = dialog.getByTestId("source-block");
      await expect(blocks).toHaveText(["Q3 2026 Highlights", "Revenue reached $1,410,000"]);
      await expect(dialog.locator('[data-cited="true"]')).toHaveText("Revenue reached $1,410,000");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("a DOCX citation shows its section, with tables rendered", async ({ page }) => {
    const email = `e2e-section-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
    const userId = await createTestUser(email, PASSWORD);
    try {
      const { documentId, chunkIds } = await seedDocumentWithChunks(userId, "report.docx", DOCX, [
        { content: "Introduction\nBackground.", section: "Introduction" },
        { content: "Quarterly Results\nTable 1 below shows revenue.", section: "Quarterly Results" },
        {
          content: "Quarterly Results\n\n| Quarter | Revenue |\n|---|---|\n| Q3 2026 | 1,410,000 |",
          section: "Quarterly Results",
          elementType: "table",
        },
      ]);
      await openCitedConversation(page, email, userId, documentId, chunkIds[2]);

      await page.getByRole("button", { name: "Show in document" }).click();

      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: "Quarterly Results" })).toBeVisible();
      await expect(dialog.getByTestId("source-block")).toHaveCount(2);
      await expect(dialog.getByText("Background.")).toHaveCount(0); // other section left out
      const cited = dialog.locator('[data-cited="true"]');
      await expect(cited.getByRole("cell", { name: "1,410,000" })).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });
});
