/**
 * Tests for Settings batch 3, part 2: permanent account deletion
 * (components/settings/danger-zone-section.tsx, delete-account-dialog.tsx).
 * Real infrastructure discipline — real local Supabase, a real running
 * backend, real browser interactions. HIGH scrutiny per the task this
 * was built from: this is the one genuinely irreversible action in the
 * app.
 */
import { test, expect, type Page } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";

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

test.describe("Settings batch 3: account deletion", () => {
  test("confirmation dialog stays disabled until both the typed email matches AND a password is entered", async ({
    page,
  }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-delete-friction");
    try {
      await page.goto("/settings");
      await page.getByRole("button", { name: "Delete account" }).click();
      await expect(page.getByText("Permanently delete your account?")).toBeVisible();

      const confirmButton = page.getByRole("button", { name: "Permanently delete account" });
      await expect(confirmButton).toBeDisabled();

      // Wrong email typed -- still disabled even with a password present.
      await page.getByRole("dialog").getByLabel(/Type .* to confirm/).fill("not-my-email@example.com");
      await page.getByRole("dialog").getByLabel("Current password").fill(PASSWORD);
      await expect(confirmButton).toBeDisabled();

      // Correct email, no password yet -- still disabled.
      await page.getByRole("dialog").getByLabel(/Type .* to confirm/).fill(email);
      await page.getByRole("dialog").getByLabel("Current password").fill("");
      await expect(confirmButton).toBeDisabled();

      // Both correct -- now enabled.
      await page.getByRole("dialog").getByLabel("Current password").fill(PASSWORD);
      await expect(confirmButton).toBeEnabled();

      // Cancel must clear both fields (not leave a stale password sitting
      // in a closed dialog's state).
      await page.getByRole("button", { name: "Keep my account" }).click();
      await expect(page.getByText("Permanently delete your account?")).not.toBeVisible();
      await page.getByRole("button", { name: "Delete account" }).click();
      await expect(page.getByRole("dialog").getByLabel(/Type .* to confirm/)).toHaveValue("");
      await expect(page.getByRole("dialog").getByLabel("Current password")).toHaveValue("");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("wrong password blocks deletion and the account survives", async ({ page }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-delete-wrongpw");
    try {
      await page.goto("/settings");
      await page.getByRole("button", { name: "Delete account" }).click();
      await page.getByRole("dialog").getByLabel(/Type .* to confirm/).fill(email);
      await page.getByRole("dialog").getByLabel("Current password").fill("definitely-the-wrong-password");
      await page.getByRole("button", { name: "Permanently delete account" }).click();

      await expect(page.getByText("Current password is incorrect.")).toBeVisible();

      // Real confirmation the account is untouched: can still sign in.
      await page.getByRole("button", { name: "Keep my account" }).click();
      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("real deletion: account is genuinely gone afterward — cannot sign in again, redirected with confirmation", async ({
    page,
  }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-delete-real");
    // No deleteTestUserByEmail cleanup needed/possible — the whole point
    // of this test is that the account no longer exists afterward.
    await page.goto("/settings");
    await page.getByRole("button", { name: "Delete account" }).click();
    await page.getByRole("dialog").getByLabel(/Type .* to confirm/).fill(email);
    await page.getByRole("dialog").getByLabel("Current password").fill(PASSWORD);
    await page.getByRole("button", { name: "Permanently delete account" }).click();

    await page.waitForURL("**/login**", { timeout: 15000 });
    await expect(page.getByText("Your account has been permanently deleted.")).toBeVisible();

    // Real proof the account is gone: this exact email/password combo
    // can never sign in again.
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Invalid login credentials")).toBeVisible();
  });
});
