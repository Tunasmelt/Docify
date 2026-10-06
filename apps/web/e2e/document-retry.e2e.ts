/**
 * Failed documents show why they failed and can be retried (2026-10-06).
 *
 * Before this, a failed document showed only a "Failed" badge — no reason,
 * and no way to trigger POST /reindex, so the stuck-document reaper's
 * recovery path was unreachable from the UI.
 *
 * Runs against the local Supabase stack and a live local FastAPI backend.
 */
import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedFailedDocument } from "./_seed";

const PASSWORD = "test-password-123";

test.describe("Failed document recovery", () => {
  test("shows the failure reason and Retry sends the document back to processing", async ({ page }) => {
    const email = `e2e-retry-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
    const userId = await createTestUser(email, PASSWORD);
    try {
      await seedFailedDocument(userId, "broken-report.pdf", "processing timed out, possibly interrupted by a service restart");

      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      const main = page.locator("main");
      await expect(main.getByText("broken-report.pdf")).toBeVisible();
      await expect(main.getByTestId("document-error")).toHaveText(
        "processing timed out, possibly interrupted by a service restart"
      );

      const reindex = page.waitForResponse(
        (res) => res.url().includes("/reindex/") && res.request().method() === "POST"
      );
      await main.getByRole("button", { name: "Retry" }).click();
      expect((await reindex).status()).toBe(202);

      // The card leaves the failed state immediately: no error, no Retry button.
      await expect(main.getByTestId("document-error")).toHaveCount(0);
      await expect(main.getByRole("button", { name: "Retry" })).toHaveCount(0);
    } finally {
      await deleteTestUserByEmail(email);
    }
  });
});
