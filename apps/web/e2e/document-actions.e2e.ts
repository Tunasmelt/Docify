import fs from "fs";
import path from "path";
import { test, expect } from "@playwright/test";
import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import { seedPdfWithChunk } from "./_seed";

test("document selection offers original viewing and a directly scoped chat", async ({ page }, testInfo) => {
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
    await dialog.getByRole("button", { name: "Close document", exact: true }).click();
    await main.getByRole("checkbox", { name: "Select preview.pdf for a conversation" }).check();
    await expect(page.getByText("1 SELECTED", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await page.locator("aside").getByRole("button", { name: "preview.pdf", exact: true }).click();
    await dialog.getByRole("link", { name: "Chat with document" }).click();
    await page.waitForURL(`**/chat/new?docs=${documentId}`);
    await expect(page.getByText("preview.pdf", { exact: true }).first()).toBeVisible();
    await page.getByRole("link", { name: "Documents", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    const reprocess = page.getByRole("button", { name: "Reprocess preview.pdf", exact: true });
    await expect(reprocess).toBeVisible();
    const row = reprocess.locator("..");
    expect(await row.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("document-reprocess-mobile.png"), animations: "disabled" });
    const queued = page.waitForResponse((r) => r.url().endsWith(`/reindex/${documentId}`) && r.request().method() === "POST");
    await page.getByRole("button", { name: "Reprocess preview.pdf", exact: true }).click();
    expect((await queued).status()).toBe(202);
    await expect(page.getByRole("button", { name: "Reprocess preview.pdf", exact: true })).toHaveCount(0);
  } finally {
    await deleteTestUserByEmail(email);
  }
});
