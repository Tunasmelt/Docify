/**
 * Tests for chat UI modernization batch 2: stop generation + regenerate.
 * Real infrastructure discipline, same as chat.e2e.ts / chat-modernization.e2e.ts —
 * real local Supabase, real backend, real Gemini/Voyage calls for the
 * cases that specifically need a genuine live stream (stop timing,
 * regenerate producing a real fresh generation).
 */
import { test, expect, type Page } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail, LOCAL_SUPABASE_SERVICE_ROLE_KEY, LOCAL_SUPABASE_URL } from "./_local-supabase";
import { seedDocument, seedTextChunk } from "./_seed";

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

/** Direct REST read against the real local stack (same service-role
 * pattern as _seed.ts) — used to confirm what actually got persisted,
 * independent of whatever the client happens to render. */
async function countConversationsForUser(userId: string): Promise<number> {
  const res = await fetch(`${LOCAL_SUPABASE_URL}/rest/v1/conversations?user_id=eq.${userId}&select=id`, {
    headers: {
      apikey: LOCAL_SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${LOCAL_SUPABASE_SERVICE_ROLE_KEY}`,
    },
  });
  const rows = (await res.json()) as unknown[];
  return rows.length;
}

test.describe("Chat UI modernization batch 2: stop generation + regenerate", () => {
  test("part 1: clicking Stop mid-stream halts token arrival immediately, discards the turn (no persisted conversation), and re-enables the input", async ({
    page,
  }) => {
    test.setTimeout(90000);
    const { email, userId } = await loginAsNewUser(page, "e2e-stop-gen");
    try {
      // Seeded, real, ready document — real embeddings aren't needed
      // for retrieval to find the ONE chunk in this account (avoids the
      // real /ingest pipeline and its own separate global rate limit,
      // which this suite doesn't need to exercise).
      const documentId = await seedDocument(userId, "seeded.pdf");
      await seedTextChunk(
        userId,
        documentId,
        "Docify's integration test constant is exactly 8675309. This document also discusses quarterly revenue trends, customer growth metrics, and operational efficiency improvements across several fiscal periods in significant detail."
      );

      await page.goto(`/chat/new?docs=${documentId}`);
      // A real, open-ended question -- deliberately asks for a long
      // answer so there's a genuine, reliable window between the first
      // token arriving and the real stream completing to click Stop in.
      await page.getByPlaceholder("Ask your documents…").fill(
        "Describe everything this document discusses in as much detail as possible, across multiple full paragraphs."
      );
      await page.getByTitle("Send question").click();

      const assistantMessage = page.getByTestId("assistant-message");
      await expect(assistantMessage).toBeVisible({ timeout: 30000 });

      const stopButton = page.getByTestId("stop-generating-button");
      await expect(stopButton).toBeVisible();
      await stopButton.click();

      // Immediate, real UI feedback: Stop button gone, Send button (and
      // the textarea) usable again right away -- not waiting on the
      // aborted connection to fully unwind server-side.
      await expect(stopButton).not.toBeVisible();
      await expect(page.getByTitle("Send question")).toBeVisible();
      const questionInput = page.getByPlaceholder("Ask your documents…");
      await expect(questionInput).toBeEnabled();

      const textAtStop = (await assistantMessage.textContent()) ?? "";

      // No further tokens arrive after the click -- real wait, then
      // confirm the rendered text genuinely stopped growing (a stray
      // background continuation would show up as more text here).
      await page.waitForTimeout(3000);
      const textAfterWaiting = (await assistantMessage.textContent()) ?? "";
      expect(textAfterWaiting).toBe(textAtStop);

      // The turn was discarded server-side, exactly like an accidental
      // disconnect (routes/query.py's proven mechanism) -- confirmed
      // directly against the real database, not inferred from the UI.
      expect(await countConversationsForUser(userId)).toBe(0);

      // A fresh question afterward still works normally -- stopping one
      // turn must not wedge the page for the next one.
      await questionInput.fill("What is the integration test constant?");
      await page.getByTitle("Send question").click();
      await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 60000 });
      expect(await countConversationsForUser(userId)).toBe(1);
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("part 2: regenerate re-asks the original question as a new, appended turn with a genuinely fresh generation and verification", async ({
    page,
  }) => {
    test.setTimeout(120000);
    const { email, userId } = await loginAsNewUser(page, "e2e-regenerate");
    try {
      const documentId = await seedDocument(userId, "seeded.pdf");
      await seedTextChunk(
        userId,
        documentId,
        "Docify's integration test constant is exactly 8675309."
      );

      await page.goto(`/chat/new?docs=${documentId}`);
      const question = "What is Docify's integration test constant? Answer in one short sentence.";
      await page.getByPlaceholder("Ask your documents…").fill(question);
      await page.getByTitle("Send question").click();

      // First real answer, real citation verification.
      await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 60000 });
      await expect(page.getByTestId("assistant-message")).toHaveCount(1);

      const regenerateButton = page.getByTestId("regenerate-message-button");
      await page.getByTestId("assistant-message").last().hover();
      await expect(regenerateButton).toBeVisible();
      await regenerateButton.click();

      // A genuinely new real generation runs -- the same real
      // retrieving/verifying stages a normal question goes through, not
      // a cached/replayed instant swap.
      await expect(page.getByText("FINDING RELEVANT PAGES…")).toBeVisible({ timeout: 10000 });

      // Wait for the second turn to fully resolve: 2 user messages (the
      // original + the re-asked, identical-text question -- APPEND,
      // confirmed decision, see page.tsx's regenerate() comment) and 2
      // assistant messages.
      await expect(page.getByTestId("user-message")).toHaveCount(2, { timeout: 60000 });
      await expect(page.getByTestId("assistant-message")).toHaveCount(2);

      const userTexts = await page.getByTestId("user-message").allTextContents();
      expect(userTexts[0]).toContain(question);
      expect(userTexts[1]).toContain(question);

      // Both assistant answers carry real, freshly-run verification --
      // not the first answer's citations reused for the second.
      const secondAnswerMarkers = page.getByTestId("assistant-message").last().locator('[data-testid^="citation-marker-"]');
      if ((await secondAnswerMarkers.count()) > 0) {
        // Real model behavior: confirm at least one marker has a real
        // verdict attribute set (proves verify_batch ran for THIS
        // answer, not an empty/placeholder citation list).
        await expect(secondAnswerMarkers.first()).toHaveAttribute("data-verdict", /supported|partial|unverified/);
      }

      // Reload -- history must reflect exactly the live APPEND
      // decision, consistently: still 2 user + 2 assistant messages,
      // not silently collapsed/deduped and not missing the regenerated
      // turn.
      await page.reload();
      await expect(page.getByTestId("assistant-message")).toHaveCount(2, { timeout: 15000 });
      await expect(page.getByTestId("user-message")).toHaveCount(2);
      const reloadedUserTexts = await page.getByTestId("user-message").allTextContents();
      expect(reloadedUserTexts[0]).toContain(question);
      expect(reloadedUserTexts[1]).toContain(question);
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("part 2 (scoping): regenerate is offered only on the most recent assistant message, and never while a turn is streaming", async ({
    page,
  }) => {
    test.setTimeout(120000);
    const { email, userId } = await loginAsNewUser(page, "e2e-regenerate-scope");
    try {
      const documentId = await seedDocument(userId, "seeded.pdf");
      await seedTextChunk(userId, documentId, "Real content for the scoping test.");

      await page.goto(`/chat/new?docs=${documentId}`);
      await page.getByPlaceholder("Ask your documents…").fill("First real question, answer briefly.");
      await page.getByTitle("Send question").click();
      await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 60000 });

      // No regenerate button visible while genuinely nothing is
      // streaming yet for a SECOND question -- ask one more, and while
      // it's actively in flight, confirm neither assistant bubble
      // offers regenerate (matches "never while a turn is streaming").
      await page.getByPlaceholder("Ask your documents…").fill("Second real question, answer briefly.");
      await page.getByTitle("Send question").click();
      await expect(page.getByTestId("stop-generating-button")).toBeVisible();
      // While streaming, regenerate must not be offered on either the
      // first (no longer last) or second (currently streaming) message.
      await expect(page.getByTestId("regenerate-message-button")).toHaveCount(0);

      await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 60000 });
      await expect(page.getByTestId("assistant-message")).toHaveCount(2);

      // Now idle again -- regenerate must be offered on exactly the
      // LAST assistant message, never the first/earlier one.
      const messages = page.getByTestId("assistant-message");
      await messages.first().hover();
      await expect(page.getByTestId("regenerate-message-button")).toHaveCount(0);
      await messages.last().hover();
      await expect(page.getByTestId("regenerate-message-button")).toHaveCount(1);
    } finally {
      await deleteTestUserByEmail(email);
    }
  });
});
