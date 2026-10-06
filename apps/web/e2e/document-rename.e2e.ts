/**
 * Documents can be renamed from the library (2026-10-06, PATCH /documents/{id}).
 *
 * Runs against the local Supabase stack and a live local FastAPI backend.
 */
import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedDocument } from "./_seed";

const PASSWORD = "test-password-123";

test.describe("Document rename", () => {
  test("renaming a document updates the library and survives a reload", async ({ page }) => {
    const email = `e2e-rename-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
    const userId = await createTestUser(email, PASSWORD);
    try {
      await seedDocument(userId, "scan_0042.pdf");

      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      const main = page.locator("main");
      await main.getByRole("button", { name: "Rename scan_0042.pdf" }).click();

      const field = page.getByRole("dialog").getByLabel("Document name");
      await expect(field).toHaveValue("scan_0042.pdf");
      await field.fill("Q3 board report.pdf");
      const patch = page.waitForResponse((res) => res.request().method() === "PATCH" && res.url().includes("/documents/"));
      await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
      expect((await patch).status()).toBe(200);

      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(main.getByText("Q3 board report.pdf")).toBeVisible();
      await expect(main.getByText("scan_0042.pdf")).toHaveCount(0);

      await page.reload();
      await expect(page.locator("main").getByText("Q3 board report.pdf")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });
});
