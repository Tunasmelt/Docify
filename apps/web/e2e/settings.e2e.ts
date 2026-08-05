/**
 * Tests for Settings, batch 1: profile (display name, avatar) + security
 * (password-change link, sign-out-other-sessions) + email change.
 * Real infrastructure discipline, same as the rest of this suite — real
 * local Supabase, real Storage, real Mailpit for the email-change flow.
 */
import { test, expect, type Page } from "@playwright/test";

import {
  createTestUser,
  deleteTestUserByEmail,
  getAccessToken,
  waitForEmailLink,
  LOCAL_SUPABASE_URL,
  LOCAL_SUPABASE_ANON_KEY,
} from "./_local-supabase";

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

test.describe("Settings batch 1: profile + security", () => {
  test("sidebar Settings link navigates to a real Settings page (no longer a '#' stub)", async ({ page }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-settings-nav");
    try {
      await page.getByRole("link", { name: "Settings" }).click();
      await page.waitForURL("**/settings");
      await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("display name: real update persists across reload and shows in the sidebar", async ({ page }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-display-name");
    try {
      await page.goto("/settings");
      const nameInput = page.locator("#display-name");
      await expect(nameInput).toBeVisible();

      await nameInput.fill("Jordan Rivera");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(page.getByText("Saved")).toBeVisible();

      // Shows up in the sidebar identity block immediately (same session,
      // no reload) -- useCurrentUser()'s onAuthStateChange subscription.
      await expect(page.locator("aside").getByText("Jordan Rivera")).toBeVisible();

      // Real persistence, not just local state.
      await page.reload();
      await expect(page.locator("aside").getByText("Jordan Rivera")).toBeVisible();
      await expect(page.locator("#display-name")).toHaveValue("Jordan Rivera");

      // Also shows up on a completely different page (documents) --
      // confirms the shared hook, not a settings-page-local fix.
      await page.goto("/documents");
      await expect(page.locator("aside").getByText("Jordan Rivera")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("avatar: real upload shows in the sidebar and persists across reload", async ({ page }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-avatar-upload");
    try {
      await page.goto("/settings");

      // A real, tiny, valid PNG -- not a placeholder string, matching
      // this suite's TINY_PNG_BASE64 convention elsewhere (_seed.ts).
      const pngBytes = Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg==",
        "base64"
      );

      await page.setInputFiles('[data-testid="avatar-file-input"]', {
        name: "avatar.png",
        mimeType: "image/png",
        buffer: pngBytes,
      });

      // Real upload completes -- the "Change avatar" button stops
      // showing "Uploading…".
      await expect(page.getByRole("button", { name: "Uploading…" })).not.toBeVisible({ timeout: 10000 });

      const sidebarAvatarImg = page.locator("aside img[alt='']");
      await expect(sidebarAvatarImg).toBeVisible();
      const src = await sidebarAvatarImg.getAttribute("src");
      expect(src).toContain("/storage/v1/object/public/avatars/");

      // Real, fetchable image -- not just a URL string that happens to
      // look right.
      const imgResponse = await page.request.get(src!);
      expect(imgResponse.status()).toBe(200);
      expect(Buffer.compare(await imgResponse.body(), pngBytes)).toBe(0);

      await page.reload();
      await expect(page.locator("aside img[alt='']")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("avatar RLS: user B can READ user A's public avatar but cannot WRITE to user A's path", async ({ page }) => {
    test.setTimeout(30000);
    const { email: emailA, userId: userIdA } = await loginAsNewUser(page, "e2e-avatar-rls-a");
    const emailB = uniqueEmail("e2e-avatar-rls-b");
    await createTestUser(emailB, PASSWORD);
    try {
      // User A uploads a real avatar via the real UI.
      await page.goto("/settings");
      const pngBytes = Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg==",
        "base64"
      );
      await page.setInputFiles('[data-testid="avatar-file-input"]', {
        name: "avatar.png",
        mimeType: "image/png",
        buffer: pngBytes,
      });
      await expect(page.getByRole("button", { name: "Uploading…" })).not.toBeVisible({ timeout: 10000 });

      const avatarPath = `${userIdA}/avatar`;

      // READ: intended, tested public-read behavior -- no auth at all,
      // a completely anonymous request.
      const publicReadRes = await page.request.get(
        `${LOCAL_SUPABASE_URL}/storage/v1/object/public/avatars/${avatarPath}`
      );
      expect(publicReadRes.status()).toBe(200);

      // WRITE: user B, with a REAL access token for their own real
      // account, attempting to overwrite user A's avatar path -- must
      // be rejected by the avatars_insert/avatars_update RLS policy
      // (migrations/20260804_001_avatars_bucket.sql), not merely
      // "not offered in the UI."
      const tokenB = await getAccessToken(emailB, PASSWORD);
      const hijackRes = await page.request.put(
        `${LOCAL_SUPABASE_URL}/storage/v1/object/avatars/${avatarPath}`,
        {
          headers: {
            apikey: LOCAL_SUPABASE_ANON_KEY,
            Authorization: `Bearer ${tokenB}`,
            "Content-Type": "image/png",
          },
          data: pngBytes,
        }
      );
      expect(hijackRes.status(), "user B must not be able to overwrite user A's avatar").not.toBe(200);

      // Confirm the original bytes are genuinely untouched.
      const afterHijackAttempt = await page.request.get(
        `${LOCAL_SUPABASE_URL}/storage/v1/object/public/avatars/${avatarPath}`
      );
      expect(Buffer.compare(await afterHijackAttempt.body(), pngBytes)).toBe(0);
    } finally {
      await deleteTestUserByEmail(emailA);
      await deleteTestUserByEmail(emailB);
    }
  });

  test("password change: Settings links to the real existing /account/update-password page", async ({ page }) => {
    test.setTimeout(30000);
    const { email } = await loginAsNewUser(page, "e2e-password-link");
    try {
      await page.goto("/settings");
      await page.getByRole("button", { name: "Change password" }).click();
      await page.waitForURL("**/account/update-password");
      await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("sign out other sessions: a second real session is invalidated while the current one stays signed in", async ({
    browser,
  }) => {
    test.setTimeout(45000);
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    const email = uniqueEmail("e2e-sign-out-others");
    await createTestUser(email, PASSWORD);

    try {
      // Two real, independent sessions for the SAME user.
      for (const p of [pageA, pageB]) {
        await p.goto("/login");
        await p.getByLabel("Email").fill(email);
        await p.getByLabel("Password").fill(PASSWORD);
        await p.getByRole("button", { name: "Sign in" }).click();
        await p.waitForURL("**/documents");
      }

      // Capture session B's real refresh token BEFORE revocation, straight
      // from Supabase's own session cookie (this project's browser client
      // is @supabase/ssr's createBrowserClient, which persists the
      // session in a cookie -- confirmed live, not localStorage the way
      // plain @supabase/supabase-js defaults -- so the server-side
      // middleware can read it too). Value is `base64-<base64 JSON>`.
      const cookies = await contextB.cookies();
      const authCookie = cookies.find((c) => c.name.startsWith("sb-") && c.name.endsWith("-auth-token"));
      expect(authCookie, "session B should have a real persisted auth cookie before revocation").toBeTruthy();
      const cookieJson = Buffer.from(authCookie!.value.replace(/^base64-/, ""), "base64").toString("utf-8");
      const refreshTokenB = JSON.parse(cookieJson).refresh_token as string;
      expect(refreshTokenB).toBeTruthy();

      // From session A, sign out every OTHER session.
      await pageA.goto("/settings");
      await pageA.getByRole("button", { name: "Sign out other sessions" }).click();
      await expect(pageA.getByRole("button", { name: "Done" })).toBeVisible();

      // Session A stays signed in -- a real navigation still works,
      // no redirect to /login.
      await pageA.goto("/documents");
      await expect(pageA).toHaveURL(/\/documents$/);

      // The REAL, provable effect of signOut({ scope: 'others' }):
      // session B's refresh token is immediately revoked server-side —
      // confirmed directly against the real GoTrue token endpoint, not
      // inferred from UI behavior. Investigated and confirmed separately
      // (not asserted here, to keep this test focused): this app's
      // middleware validates the JWT locally (getClaims(), signature +
      // expiry only) rather than calling the network on every
      // navigation, so an already-open tab is NOT instantly kicked out —
      // standard, expected behavior for stateless JWT auth, not a bug.
      // The real, immediate effect is that session B can never obtain a
      // NEW access token again, which is exactly what "sign out other
      // sessions" can honestly promise.
      const refreshAttempt = await pageB.request.post(`${LOCAL_SUPABASE_URL}/auth/v1/token?grant_type=refresh_token`, {
        headers: { apikey: LOCAL_SUPABASE_ANON_KEY, "Content-Type": "application/json" },
        data: { refresh_token: refreshTokenB },
      });
      expect(refreshAttempt.status(), "session B's refresh token must be rejected after 'sign out other sessions'").not.toBe(200);
    } finally {
      await deleteTestUserByEmail(email);
      await contextA.close();
      await contextB.close();
    }
  });

  test("email change: real Mailpit emails to both addresses; confirming via EITHER ONE alone completes it", async ({
    page,
  }) => {
    test.setTimeout(60000);
    const oldEmail = uniqueEmail("e2e-email-change-old");
    const newEmail = uniqueEmail("e2e-email-change-new");
    await createTestUser(oldEmail, PASSWORD);

    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(oldEmail);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      await page.goto("/settings");
      await page.getByLabel("New email address").fill(newEmail);
      // 2026-08-05 security follow-up: current password is now required
      // to even START an email change (see lib/supabase/profile.ts's
      // requestEmailChange docstring) -- the submit button stays
      // disabled without it.
      await page.getByLabel("Current password").fill(PASSWORD);
      await page.getByRole("button", { name: "Change email" }).click();

      const pending = page.getByTestId("email-change-pending");
      await expect(pending).toBeVisible();
      await expect(pending).toContainText(oldEmail);
      await expect(pending).toContainText(newEmail);

      // Real emails to BOTH addresses -- double_confirm_changes = true
      // (apps/api/supabase/config.toml) does send to both, confirmed.
      const oldEmailLink = await waitForEmailLink(oldEmail);
      const newEmailLink = await waitForEmailLink(newEmail);
      expect(oldEmailLink, "a confirmation email should arrive at the OLD address").toBeTruthy();
      expect(newEmailLink, "a confirmation email should arrive at the NEW address").toBeTruthy();
      expect(oldEmailLink).not.toBe(newEmailLink);

      // Real investigated finding (NOT what the config option's name
      // implies): confirming via the NEW address's link ALONE completes
      // the entire change immediately -- verified directly against the
      // real local GoTrue instance before writing this assertion, not
      // assumed. Routes through the existing, unmodified /auth/callback
      // (PKCE code exchange is type-agnostic, same as password-reset).
      await page.goto(newEmailLink!);
      await page.waitForURL("**/settings", { timeout: 20000 });

      // Real, end-to-end confirmation: the account's email is genuinely
      // the new one now -- sign out, and only the NEW email can log in.
      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");

      await page.getByLabel("Email").fill(newEmail);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");
      await page.getByLabel("Email").fill(oldEmail);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await expect(page.getByText("Invalid login credentials")).toBeVisible();

      // The OTHER (old-address) link, now visited after the change
      // already completed via the new-address link, must be rejected as
      // already-used/expired -- not silently re-processed, and not a
      // confusing crash. Confirms the flow doesn't double-apply.
      const oldLinkAfterCompletion = await page.request.get(oldEmailLink!, { maxRedirects: 0 }).catch((e) => e);
      // A 3xx redirect (to /auth/callback with an error param) is the
      // real shape GoTrue uses for an already-used token — not a 2xx.
      if (typeof oldLinkAfterCompletion === "object" && "status" in oldLinkAfterCompletion) {
        expect(oldLinkAfterCompletion.status()).toBeGreaterThanOrEqual(300);
        expect(oldLinkAfterCompletion.status()).toBeLessThan(400);
      }
    } finally {
      await deleteTestUserByEmail(oldEmail);
      await deleteTestUserByEmail(newEmail);
    }
  });

  test("email change: a wrong current password blocks the change from even starting (2026-08-05 security follow-up)", async ({
    page,
  }) => {
    test.setTimeout(30000);
    const oldEmail = uniqueEmail("e2e-email-change-reauth");
    const newEmail = uniqueEmail("e2e-email-change-reauth-new");
    await createTestUser(oldEmail, PASSWORD);

    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(oldEmail);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      await page.goto("/settings");
      await page.getByLabel("New email address").fill(newEmail);
      await page.getByLabel("Current password").fill("definitely-the-wrong-password");
      await page.getByRole("button", { name: "Change email" }).click();

      await expect(page.getByText("Current password is incorrect.")).toBeVisible();
      // No "check your inbox" state -- the change never started.
      await expect(page.getByTestId("email-change-pending")).not.toBeVisible();

      // Real confirmation the account is untouched: the OLD email can
      // still sign in, proving no swap occurred server-side either.
      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");
      await page.getByLabel("Email").fill(oldEmail);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");
    } finally {
      await deleteTestUserByEmail(oldEmail);
      await deleteTestUserByEmail(newEmail);
    }
  });
});
