import fs from "fs";
import path from "path";
import { test, expect } from "@playwright/test";
import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedPdfWithChunk } from "./_seed";

test("document selection offers original viewing and a directly scoped chat", async ({ page }) => {
  test.setTimeout(120000);
  const email = `e2e-document-actions-${Date.now()}@example.com`;
  const password = "test-password-123";
  const userId = await createTestUser(email, password);
  try {
    const { documentId } = await seedPdfWithChunk(userId,
      fs.readFileSync(path.join(process.cwd(), "..", "api", "tests", "fixtures", "clean_digital.pdf")),
      "A document to view and chat with.", { x0: 56, y0: 120, x1: 540, y1: 160 });
    await page.goto("/login");
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.waitForURL("**/documents");
    const main = page.locator("main");
    await main.getByRole("button", { name: "Open preview.pdf" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("link", { name: "Chat with document" })).toHaveAttribute("href", `/chat/new?docs=${documentId}`);
    await dialog.getByRole("button", { name: "View document", exact: true }).click();
    await expect(dialog.locator("iframe")).toBeVisible();
    const originalUrl = await dialog.getByRole("link", { name: "Open original in a new tab" }).getAttribute("href");
    const original = await page.request.get(originalUrl!);
    expect(original.status()).toBe(200);
    expect((await original.body()).subarray(0, 4).toString()).toBe("%PDF");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await main.getByRole("checkbox", { name: "Select preview.pdf for a conversation" }).check();
    await expect(page.getByText("1 SELECTED", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await page.locator("aside").getByRole("button", { name: "preview.pdf", exact: true }).click();
    await dialog.getByRole("link", { name: "Chat with document" }).click();
    await page.waitForURL(`**/chat/new?docs=${documentId}`);
    await expect(page.getByText("preview.pdf", { exact: true }).first()).toBeVisible();
  } finally {
    await deleteTestUserByEmail(email);
  }
});
