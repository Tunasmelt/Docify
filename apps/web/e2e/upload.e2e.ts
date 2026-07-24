/**
 * Tests for [FEAT-014] Upload UI + document list
 * Runs against the LOCAL Supabase stack AND a live local FastAPI backend
 * (uvicorn pointed at the same local stack — see FEATURES.md's "Run"
 * section for FEAT-014). Never point either at production.
 *
 * Locators are consistently scoped to `<main>` (or the dialog) rather than
 * the whole page: filenames and empty-state copy legitimately appear in
 * both the sidebar library list AND the main content area at once, which
 * a bare page-wide getByText resolves ambiguously even in an otherwise-
 * normal steady state. Confirmed live that Playwright's strict-mode
 * rejection of a multi-match locator is not reliably tolerated by
 * `toBeVisible()`/`not.toBeVisible()` either way — scoping explicitly
 * throughout avoids depending on that undocumented edge behavior.
 */
import path from "path";

import { test, expect } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";

const FIXTURE_PDF = path.join(process.cwd(), "..", "api", "tests", "fixtures", "clean_digital.pdf");
const PASSWORD = "test-password-123";

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

async function loginAsNewUser(page: import("@playwright/test").Page, emailPrefix: string) {
  const email = uniqueEmail(emailPrefix);
  await createTestUser(email, PASSWORD);
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/documents");
  return email;
}

test.describe("[FEAT-014] Upload UI + document list", () => {
  test("click-to-select uploads a real PDF; appears immediately with the real 'Uploaded' status", async ({
    page,
  }) => {
    const email = await loginAsNewUser(page, "e2e-upload");
    try {
      const main = page.locator("main");
      await page.locator('input[type="file"]').setInputFiles(FIXTURE_PDF);

      // Real upload in flight — no fabricated percentage (the Storage SDK
      // has no upload-progress API), just an honest indeterminate state.
      await expect(page.getByText("UPLOADING…")).toBeVisible();

      // API_CONTRACT.md (corrected 2026-07-23, FEAT-007): POST /ingest's
      // 202 response reflects the just-inserted row's literal status,
      // which is 'uploaded' — NOT 'parsing' (an earlier draft example got
      // this wrong; 'parsing' is set moments later inside the background
      // task, after the response has already gone out). This test asserts
      // the real contract, not the scaffold's stale acceptance-criterion
      // wording.
      await expect(main.getByText("clean_digital.pdf")).toBeVisible({ timeout: 15000 });
      await expect(main.getByText("Uploaded", { exact: true })).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("status updates via polling until the document reaches Ready, with no page reload", async ({
    page,
  }) => {
    test.setTimeout(120000);
    const email = await loginAsNewUser(page, "e2e-poll");
    try {
      const main = page.locator("main");
      await page.locator('input[type="file"]').setInputFiles(FIXTURE_PDF);
      await expect(main.getByText("clean_digital.pdf")).toBeVisible({ timeout: 15000 });

      // Real Docling parse + Voyage embed running server-side in the
      // background task — polling (not a reload) must surface every
      // transition. Playwright's own expect-polling drives the wait.
      await expect(main.getByText("Ready", { exact: true })).toBeVisible({ timeout: 90000 });
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("delete confirmation modal appears, and a real DELETE removes the document (persists after reload)", async ({
    page,
  }) => {
    test.setTimeout(120000);
    const email = await loginAsNewUser(page, "e2e-delete");
    try {
      const main = page.locator("main");
      await page.locator('input[type="file"]').setInputFiles(FIXTURE_PDF);
      await expect(main.getByText("clean_digital.pdf")).toBeVisible({ timeout: 15000 });
      await expect(main.getByText("Ready", { exact: true })).toBeVisible({ timeout: 90000 });

      await page.getByTitle("Delete").click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByText("Delete this document?")).toBeVisible();
      await expect(dialog.getByText("clean_digital.pdf")).toBeVisible();

      await dialog.getByRole("button", { name: "Delete document" }).click();

      await expect(main.getByText("clean_digital.pdf")).not.toBeVisible();
      await expect(main.getByText("No documents yet.")).toBeVisible();

      // Reload to prove this is a real, persisted deletion — not just
      // optimistic local state that would reappear on a fresh GET /documents.
      await page.reload();
      await expect(main.getByText("No documents yet.")).toBeVisible();
      await expect(main.getByText("clean_digital.pdf")).not.toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("empty state renders for a fresh user with no documents", async ({ page }) => {
    const email = await loginAsNewUser(page, "e2e-empty");
    try {
      const main = page.locator("main");
      await expect(main.getByText("No documents yet.")).toBeVisible();
      await expect(main.getByText("0 DOCUMENTS")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("error state when upload fails (non-PDF rejected client-side before any network call)", async ({
    page,
  }) => {
    const email = await loginAsNewUser(page, "e2e-uploaderr");
    try {
      const textFile = path.join(process.cwd(), "e2e", "_local-supabase.ts");
      await page.locator('input[type="file"]').setInputFiles(textFile);
      await expect(page.getByText("Only PDF files are supported.")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("multi-tenant: two real users each see only their own documents", async ({ browser }) => {
    test.setTimeout(60000);
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    const emailA = await loginAsNewUser(pageA, "e2e-tenant-a");
    const emailB = await loginAsNewUser(pageB, "e2e-tenant-b");

    try {
      const mainA = pageA.locator("main");
      const mainB = pageB.locator("main");

      await pageA.locator('input[type="file"]').setInputFiles(FIXTURE_PDF);
      await expect(mainA.getByText("clean_digital.pdf")).toBeVisible({ timeout: 15000 });

      // User B never uploaded anything — must never see user A's document,
      // structurally guaranteed by FEAT-008's user_id-scoped queries, but
      // worth one real confirmation at the UI layer.
      await expect(mainB.getByText("No documents yet.")).toBeVisible();
      await expect(mainB.getByText("clean_digital.pdf")).not.toBeVisible();

      await pageB.reload();
      await expect(mainB.getByText("No documents yet.")).toBeVisible();
    } finally {
      await deleteTestUserByEmail(emailA);
      await deleteTestUserByEmail(emailB);
      await contextA.close();
      await contextB.close();
    }
  });
});
