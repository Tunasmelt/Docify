/**
 * "Open page N in document" shows the cited PDF page with the cited passage
 * highlighted (2026-10-06). The button used to be dead: its handler only
 * logged "[not yet built]" to the console.
 *
 * Runs against the local Supabase stack and a live local FastAPI backend,
 * which renders the page server-side.
 */
import fs from "fs";
import path from "path";

import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedConversationTurn, seedPdfWithChunk } from "./_seed";

const FIXTURE_PDF = path.join(process.cwd(), "..", "api", "tests", "fixtures", "clean_digital.pdf");
const PASSWORD = "test-password-123";

test.describe("Citation page preview", () => {
  test("opens the cited PDF page with the passage highlighted", async ({ page }) => {
    const email = `e2e-preview-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
    const userId = await createTestUser(email, PASSWORD);
    try {
      const { documentId, chunkId } = await seedPdfWithChunk(
        userId,
        fs.readFileSync(FIXTURE_PDF),
        "This is a simple document created to test basic PDF functionality.",
        { x0: 56, y0: 120, x1: 540, y1: 160 }
      );
      const { conversationId } = await seedConversationTurn({
        userId,
        documentIds: [documentId],
        question: "What is this document?",
        answerContent: "It is a simple test document [1].",
        citations: [
          {
            chunk_id: chunkId,
            marker: 1,
            claim_span: "It is a simple test document",
            verdict: "supported",
            supporting_quote: "This is a simple document",
          },
        ],
      });

      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      await page.goto(`/chat/${conversationId}`);
      await page.locator('[data-verdict="supported"]').first().click();

      const imageRequest = page.waitForResponse((res) => res.url().includes(`/documents/${documentId}/pages/1/image`));
      await page.getByRole("button", { name: "Open page 1 in document" }).click();

      const response = await imageRequest;
      expect(response.status()).toBe(200);
      const url = new URL(response.url());
      expect(url.searchParams.get("x0")).toBe("56");
      expect(url.searchParams.get("y1")).toBe("160");

      const image = page.getByTestId("page-preview-image");
      await expect(image).toBeVisible();
      const naturalWidth = await image.evaluate((img) => (img as HTMLImageElement).naturalWidth);
      expect(naturalWidth).toBeGreaterThan(500);
    } finally {
      await deleteTestUserByEmail(email);
    }
  });
});
