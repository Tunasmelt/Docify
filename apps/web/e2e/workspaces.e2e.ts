import { test, expect } from "@playwright/test";
import { createTestUser, deleteTestUserByEmail, getAccessToken } from "./_local-supabase";
import { seedDocument, seedConversationTurn } from "./_seed";

test("private workspaces scope documents and chats and persist selection", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const email = `e2e-workspaces-${Date.now()}@example.com`;
  const password = "test-password-123";
  const userId = await createTestUser(email, password);
  try {
    const documentId = await seedDocument(userId, "workspace-original.pdf");
    await seedConversationTurn({ userId, documentIds: [documentId], question: "Original workspace chat", answerContent: "Seeded answer", citations: [] });
    await page.goto("/login");
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.waitForURL("**/documents");
    const selector = page.getByLabel("Workspace", { exact: true });
    await expect(page.getByText("workspace-original.pdf", { exact: true }).first()).toBeVisible();
    const original = await selector.inputValue();
    await page.screenshot({ path: testInfo.outputPath("workspace-light.png"), animations: "disabled" });
    await page.getByRole("button", { name: "Dark mode", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.screenshot({ path: testInfo.outputPath("workspace-dark.png"), animations: "disabled" });
    await page.getByRole("button", { name: "New", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "New workspace" })).toBeVisible();
    await expect(page.getByLabel("Workspace name")).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath("workspace-create.png"), animations: "disabled" });
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "New", exact: true }).click();
    await page.getByLabel("Workspace name").fill("Research");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(selector.locator("option:checked")).toHaveText("Research");
    await expect(page.getByText("workspace-original.pdf", { exact: true })).toHaveCount(0);
    await page.reload();
    await expect(selector.locator("option:checked")).toHaveText("Research");
    await page.getByRole("link", { name: "Conversations", exact: true }).click();
    await expect(page.getByText("No conversations yet.")).toBeVisible();
    await expect(page.getByText("Original workspace chat", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await page.getByLabel("Workspace name").fill("Renamed research");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(selector.locator("option:checked")).toHaveText("Renamed research");
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("button", { name: "Confirm delete", exact: true }).click();
    await expect(selector).toHaveValue(original);
    await expect(page.getByText("workspace-original.pdf", { exact: true }).first()).toBeVisible();
    await page.getByRole("link", { name: "Conversations", exact: true }).click();
    await expect(page.getByText("Original workspace chat", { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("header button").first().click();
    await expect(selector).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("workspace-mobile.png"), animations: "disabled" });
    await page.getByRole("button", { name: "New", exact: true }).click();
    const mobileDialog = page.getByRole("dialog", { name: "New workspace" });
    await expect(mobileDialog).toBeVisible();
    const bounds = await mobileDialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: testInfo.outputPath("workspace-mobile-dialog.png"), animations: "disabled" });
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
  } finally {
    await deleteTestUserByEmail(email);
  }
});

test("test-user cleanup preserves other local accounts", async () => {
  const first = `e2e-workspaces-cleanup-a-${Date.now()}@example.com`;
  const second = `e2e-workspaces-cleanup-b-${Date.now()}@example.com`;
  const password = "test-password-123";
  await createTestUser(first, password);
  await createTestUser(second, password);
  try {
    await deleteTestUserByEmail(first);
    expect(await getAccessToken(second, password)).toBeTruthy();
  } finally {
    await deleteTestUserByEmail(first);
    await deleteTestUserByEmail(second);
  }
});
