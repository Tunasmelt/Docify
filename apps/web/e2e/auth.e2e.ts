/**
 * Tests for [FEAT-013] Next.js app shell + Supabase Auth
 * Runs against the LOCAL Supabase stack (`supabase start`) only — never
 * point NEXT_PUBLIC_SUPABASE_URL at the live project while running these.
 */
import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";

function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

const PASSWORD = "test-password-123";

test.describe("[FEAT-013] Next.js app shell + Supabase Auth", () => {
  test("login with email/password works", async ({ page }) => {
    const email = uniqueEmail();
    await createTestUser(email, PASSWORD);
    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");
      await expect(page.getByRole("heading", { name: /Documents/ })).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("signup creates a Supabase Auth user and lands in the app", async ({ page }) => {
    const email = uniqueEmail();
    try {
      await page.goto("/signup");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
      await page.getByLabel("Confirm password").fill(PASSWORD);
      await page.getByRole("button", { name: "Create account" }).click();
      await page.waitForURL("**/documents");
      await expect(page.getByRole("heading", { name: /Documents/ })).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("Google OAuth button initiates the OAuth redirect", async ({ page }) => {
    await page.goto("/login");
    const [popupOrNav] = await Promise.all([
      page.waitForURL(/accounts\.google\.com|localhost:54321\/auth\/v1\/authorize/, {
        timeout: 10_000,
      }).catch(() => null),
      page.getByRole("button", { name: "Continue with Google" }).click(),
    ]);
    expect(page.url()).toMatch(/accounts\.google\.com|\/auth\/v1\/authorize/);
    void popupOrNav;
  });

  test("protected routes redirect unauthenticated users to /login", async ({ page }) => {
    await page.goto("/documents");
    await page.waitForURL("**/login");
    expect(page.url()).toContain("/login");
  });

  test("middleware redirects authenticated users away from /login", async ({ page }) => {
    const email = uniqueEmail();
    await createTestUser(email, PASSWORD);
    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      // Re-request /login directly (not via the app's own post-login push) —
      // this exercises the middleware's own redirect-away-from-auth-paths
      // branch, not just the client-side router.push after signInWithPassword.
      await page.goto("/login");
      await page.waitForURL("**/documents");
      expect(page.url()).toContain("/documents");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("layout has navigation shell (sidebar + top bar) once authenticated", async ({ page }) => {
    const email = uniqueEmail();
    await createTestUser(email, PASSWORD);
    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");
      await expect(page.getByRole("link", { name: /Documents/ })).toBeVisible();
      await expect(page.getByRole("link", { name: /Conversations/ })).toBeVisible();
      await expect(page.getByTitle("Sign out")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("sign out terminates the session and redirects to /login", async ({ page }) => {
    const email = uniqueEmail();
    await createTestUser(email, PASSWORD);
    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      await page.getByTitle("Sign out").click();
      await page.waitForURL("**/login");

      await page.goto("/documents");
      await page.waitForURL("**/login");
      expect(page.url()).toContain("/login");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });
});
