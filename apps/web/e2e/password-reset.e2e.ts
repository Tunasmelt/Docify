/**
 * Tests for the FEAT-013 self-audit fix: real PKCE code-exchange callback
 * route (app/auth/callback/route.ts) + update-password page. Runs against
 * the LOCAL Supabase stack only — never point NEXT_PUBLIC_SUPABASE_URL at
 * the live project while running these.
 */
import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail, waitForEmailLink } from "./_local-supabase";

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

const OLD_PASSWORD = "old-password-123";
const NEW_PASSWORD = "new-password-456";

test.describe("Password reset end-to-end (real Mailpit email, real code exchange)", () => {
  test("request -> real email -> real link -> real code exchange -> real password update -> real login with new password", async ({
    page,
  }) => {
    test.setTimeout(60000);

    const email = uniqueEmail("e2e-reset");
    await createTestUser(email, OLD_PASSWORD);

    try {
      // Request the reset from the real UI.
      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByText("Forgot?").click();
      await expect(page.getByText("Password reset email sent")).toBeVisible();

      // Pick up the real email GoTrue actually sent, via the real local
      // mail catcher — not a stub, not a captured token shortcut.
      const link = await waitForEmailLink(email);
      expect(link, "a real reset email should have arrived in Mailpit").toBeTruthy();

      // Follow the real link — GoTrue verifies it, redirects to
      // /auth/callback?code=...&next=/account/update-password, which
      // exchanges the code for a real recovery session server-side.
      await page.goto(link!);
      await page.waitForURL("**/account/update-password", { timeout: 20000 });

      // Set a new password through the real form.
      await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
      await page.getByLabel("Confirm new password").fill(NEW_PASSWORD);
      await page.getByRole("button", { name: "Update password" }).click();
      await page.waitForURL("**/documents");

      // Terminate that session and prove the NEW password actually works
      // via a completely separate, fresh login — not just the recovery
      // session's own implicit auth.
      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");

      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(NEW_PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");
      await expect(page.getByRole("heading", { name: /Documents/ })).toBeVisible();

      // And confirm the OLD password no longer works.
      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(OLD_PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await expect(page.getByText("Invalid login credentials")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("an invalid/already-used code at the callback route shows a real error on /login, not a blank sign-in form", async ({
    page,
  }) => {
    await page.goto("/auth/callback?code=this-code-does-not-exist&next=/account/update-password");
    await page.waitForURL("**/login**");
    // A real, human error — not silence. Exact message comes from GoTrue's
    // own exchangeCodeForSession failure, forwarded by our callback route.
    const errorText = await page.locator("p.text-red-500").first().textContent();
    expect(errorText).toBeTruthy();
    expect(errorText!.length).toBeGreaterThan(0);
  });

  test("a stray ?code= landing directly on /login (bypassing the callback route) shows a real error, not a bare form", async ({
    page,
  }) => {
    await page.goto("/login?code=some-stale-bookmarked-code");
    await expect(page.getByText("This link is no longer valid")).toBeVisible();
  });
});
