/**
 * Tests for the landing page ("/"). Real infrastructure discipline —
 * real local Supabase, a real running backend not needed here (this
 * page makes no API calls), real browser navigation for every CTA.
 */
import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";

const PASSWORD = "test-password-123";
const REPO_URL = "https://github.com/Tunasmelt/Docify";

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

test.describe("Landing page", () => {
  test("unauthenticated visit renders the landing page with real content", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL("http://127.0.0.1:3000/");
    await expect(page.getByRole("heading", { name: /Ask your documents/ })).toBeVisible();
    await expect(page.getByText("PDF · DOCX · PPTX · HTML")).toBeVisible();
    await expect(page.getByText("Answers arrive footnoted. The footnotes are checked.")).toBeVisible();

    // The corrected citation: a real page number, never a slide number,
    // for a handbook that isn't PPTX (lib/chat/parse-message.ts's
    // citationLocation() convention).
    await expect(page.getByText("EMPLOYEE HANDBOOK 2026 · P.12")).toBeVisible();
    await expect(page.getByText("EMPLOYEE HANDBOOK 2026 · SLIDE 12")).toHaveCount(0);

    await expect(page.getByText("Four formats, one library")).toBeVisible();
    await expect(page.getByText("Start with one document.")).toBeVisible();
  });

  test("signed-in visit to / redirects straight to /documents", async ({ page }) => {
    const email = uniqueEmail("e2e-landing-redirect");
    await createTestUser(email, PASSWORD);
    try {
      await page.goto("/login");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/documents");

      // Re-request "/" directly -- exercises middleware's own
      // redirect-away-from-landing branch, not just a client push.
      await page.goto("/");
      await page.waitForURL("**/documents");
      expect(page.url()).toContain("/documents");
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("header CTAs navigate for real: Sign in -> /login, Create account -> /signup", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("banner").getByRole("link", { name: "Sign in" }).click();
    await page.waitForURL("**/login");

    await page.goto("/");
    await page.getByRole("banner").getByRole("link", { name: "Create account" }).click();
    await page.waitForURL("**/signup");
  });

  test("hero and final CTA both navigate to /signup for real", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Create an account" }).first().click();
    await page.waitForURL("**/signup");

    await page.goto("/");
    await page.getByRole("link", { name: "Create an account" }).last().click();
    await page.waitForURL("**/signup");
  });

  test("footer links navigate for real: Sign in -> /login, Create account -> /signup", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("contentinfo").getByRole("link", { name: "Sign in" }).click();
    await page.waitForURL("**/login");

    await page.goto("/");
    await page.getByRole("contentinfo").getByRole("link", { name: "Create account" }).click();
    await page.waitForURL("**/signup");
  });

  test("GitHub links point at the real repo (header, hero, footer) and open in a new tab", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("banner").getByRole("link", { name: "Source" })).toHaveAttribute("href", REPO_URL);
    await expect(page.getByRole("link", { name: "Read the source" })).toHaveAttribute("href", REPO_URL);
    await expect(page.getByRole("contentinfo").getByRole("link", { name: "Tunasmelt/Docify" })).toHaveAttribute(
      "href",
      REPO_URL
    );

    // Real click, real new tab, real destination -- not just an href string.
    const [popup] = await Promise.all([
      page.context().waitForEvent("page"),
      page.getByRole("banner").getByRole("link", { name: "Source" }).click(),
    ]);
    await popup.waitForLoadState("domcontentloaded").catch(() => {});
    expect(popup.url()).toContain("github.com/Tunasmelt/Docify");
    await popup.close();
  });

  test("How it works anchor scrolls to the product preview section", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "How it works" }).click();
    await expect(page).toHaveURL(/#how$/);
    await expect(page.getByText("Answers arrive footnoted. The footnotes are checked.")).toBeInViewport();
  });

  test("dark mode toggle works on the landing page and persists the shared theme", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.getByRole("button", { name: "Dark mode" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.getByRole("button", { name: "Light mode" })).toBeVisible();
  });
});
