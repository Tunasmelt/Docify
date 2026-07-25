/**
 * Full real-user end-to-end journey — the frontend equivalent of the
 * backend's full-flow audit (.agent/reviews/2026-07-24-full-flow.md),
 * run for the first time at this layer. One real account, through the
 * real running app, nothing mocked or faked anywhere: real signup, real
 * Docling parse, real Voyage embed, real hybrid retrieval, real Gemini
 * generation + verification, real Storage-backed figure URLs if the
 * question happens to cite one, real session termination on sign-out.
 *
 * Uses the same fixture + question pair FEAT-009/010's real quality
 * tests already proved retrieve/generate correctly on
 * (test_query_e2e.py's QUALITY_QUESTIONS) — chosen here for the same
 * reason: maximizing confidence of a real, meaningful citation on the
 * first try, not because the question is scripted or the answer is
 * hand-picked. The answer itself, and whether a citation shows up at
 * all, is genuinely whatever Gemini returns.
 *
 * Screenshots are the primary evidence artifact — saved under
 * e2e/screenshots/full-journey/ and read back after the run, not just
 * asserted on programmatically.
 */
import path from "path";

import { test, expect } from "@playwright/test";

import { deleteTestUserByEmail } from "./_local-supabase";

const FIXTURE_PDF = path.join(process.cwd(), "..", "api", "tests", "fixtures", "table_heavy.pdf");
const SCREEN_DIR = path.join(process.cwd(), "e2e", "screenshots", "full-journey");
const PASSWORD = "test-password-123";

function uniqueEmail(): string {
  return `e2e-fulljourney-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

test("full real user journey: signup -> upload -> ask -> citation -> reload -> second conversation -> sign out", async ({
  page,
}) => {
  test.setTimeout(5 * 60 * 1000);
  const email = uniqueEmail();

  try {
    // ---- Step 1: real signup ----
    await page.goto("/signup");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Confirm password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await page.waitForURL("**/documents");
    await expect(page.getByRole("heading", { name: /Documents/ })).toBeVisible();
    await page.screenshot({ path: path.join(SCREEN_DIR, "01-signup-landed-on-documents.png"), fullPage: true });

    // ---- Step 2: real upload, real parsing, real polling to Ready ----
    const main = page.locator("main");
    await page.locator('input[type="file"]').setInputFiles(FIXTURE_PDF);
    await expect(main.getByText("table_heavy.pdf")).toBeVisible({ timeout: 15000 });
    await page.screenshot({ path: path.join(SCREEN_DIR, "02a-upload-in-progress.png"), fullPage: true });
    await expect(main.getByText("Ready", { exact: true })).toBeVisible({ timeout: 120000 });
    await page.screenshot({ path: path.join(SCREEN_DIR, "02b-document-ready.png"), fullPage: true });

    // ---- Step 3+4: real question, real 3-9s+ round trip, real answer ----
    await page.getByTitle("Select for a conversation").click();
    await page.getByRole("button", { name: "Ask about these" }).click();
    await page.waitForURL(/\/chat\/new\?docs=/);

    const question1 = "What is Angola's Human Development Index value in 2010?";
    await page.getByPlaceholder("Ask your documents…").fill(question1);
    await page.getByTitle("Send question").click();
    await expect(page.getByText("FINDING RELEVANT PAGES…")).toBeVisible();
    await page.screenshot({ path: path.join(SCREEN_DIR, "03-loading-real-round-trip.png"), fullPage: true });

    await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 90000 });
    const firstConversationUrl = page.url();
    const assistantMessage = page.getByTestId("assistant-message").first();
    await expect(assistantMessage).toBeVisible();
    await expect(assistantMessage).not.toContainText("mock");
    const realAnswerText = await assistantMessage.innerText();
    await page.screenshot({ path: path.join(SCREEN_DIR, "04-real-cited-answer.png"), fullPage: true });

    // ---- Step 5 (+6 if applicable): click a citation, real source panel ----
    const citationMarkers = assistantMessage.locator('[data-testid^="citation-marker-"]');
    const citationCount = await citationMarkers.count();
    let clickedFigureCitation = false;
    if (citationCount > 0) {
      await citationMarkers.first().click();
      const panel = page.getByTestId("source-panel");
      await expect(panel).toBeVisible();
      await expect(panel).not.toBeEmpty();
      await page.waitForTimeout(400); // let the 0.25s slide-in animation settle before the evidence screenshot
      await page.screenshot({ path: path.join(SCREEN_DIR, "05-source-panel-real-content.png"), fullPage: true });

      const figureImg = page.getByTestId("figure-image");
      if (await figureImg.isVisible().catch(() => false)) {
        clickedFigureCitation = true;
        const src = await figureImg.getAttribute("src");
        expect(src).toBeTruthy();
        const imgResponse = await page.request.get(src!);
        expect(imgResponse.status()).toBe(200);
        expect((await imgResponse.body()).length).toBeGreaterThan(0);
        await page.screenshot({ path: path.join(SCREEN_DIR, "06-real-figure-image-loaded.png"), fullPage: true });
      }
      await page.getByTitle("Close").click();
    }

    // ---- Step 7: reload, confirm real history reload with correct citations ----
    const markersBeforeReload = citationCount > 0
      ? await citationMarkers.evaluateAll((els) => els.map((el) => el.textContent))
      : [];
    await page.reload();
    await expect(page.getByTestId("assistant-message").first()).toBeVisible({ timeout: 15000 });
    expect(page.url()).toBe(firstConversationUrl);
    const reloadedAnswerText = await page.getByTestId("assistant-message").first().innerText();
    expect(reloadedAnswerText).toBe(realAnswerText);
    if (citationCount > 0) {
      const reloadedMarkers = page
        .getByTestId("assistant-message")
        .first()
        .locator('[data-testid^="citation-marker-"]');
      expect(await reloadedMarkers.evaluateAll((els) => els.map((el) => el.textContent))).toEqual(markersBeforeReload);
    }
    await page.screenshot({ path: path.join(SCREEN_DIR, "07-reloaded-history-matches.png"), fullPage: true });

    // ---- Step 8: second, independent conversation ----
    await page.goto("/documents");
    await page.getByTitle("Select for a conversation").click();
    await page.getByRole("button", { name: "Ask about these" }).click();
    await page.waitForURL(/\/chat\/new\?docs=/);
    const question2 = "What was the balance in the 2011 accounts?";
    await page.getByPlaceholder("Ask your documents…").fill(question2);
    await page.getByTitle("Send question").click();
    await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 90000 });
    const secondConversationUrl = page.url();
    expect(secondConversationUrl).not.toBe(firstConversationUrl);
    await expect(page.getByTestId("assistant-message").first()).toBeVisible();

    await page.goto("/chat");
    await expect(page.getByText(question1, { exact: false })).toBeVisible();
    await expect(page.getByText(question2, { exact: false })).toBeVisible();
    await page.screenshot({ path: path.join(SCREEN_DIR, "08a-both-conversations-listed.png"), fullPage: true });

    await page.goto(firstConversationUrl);
    await expect(page.getByTestId("user-message").first()).toContainText(question1);
    await page.goto(secondConversationUrl);
    await expect(page.getByTestId("user-message").first()).toContainText(question2);
    await page.screenshot({ path: path.join(SCREEN_DIR, "08b-second-conversation-resumed.png"), fullPage: true });

    // ---- Step 9: sign out, real session termination ----
    await page.getByTitle("Sign out").click();
    await page.waitForURL("**/login");
    await page.screenshot({ path: path.join(SCREEN_DIR, "09a-signed-out-at-login.png"), fullPage: true });

    // Not just a UI redirect — the actual session must be gone. A direct
    // navigation to a protected route afterward must bounce back to
    // /login (middleware.ts's own real check), not silently render.
    await page.goto("/documents");
    await page.waitForURL("**/login");
    await page.screenshot({ path: path.join(SCREEN_DIR, "09b-direct-nav-to-documents-bounced-to-login.png"), fullPage: true });

    console.log(`\nFULL JOURNEY SUMMARY:`);
    console.log(`  citationCount on first answer: ${citationCount}`);
    console.log(`  clicked a figure citation: ${clickedFigureCitation}`);
    console.log(`  first conversation: ${firstConversationUrl}`);
    console.log(`  second conversation: ${secondConversationUrl}`);
  } finally {
    await deleteTestUserByEmail(email);
  }
});
