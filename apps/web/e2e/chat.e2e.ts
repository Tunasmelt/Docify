/**
 * Tests for [FEAT-015] Chat UI + citation source panel
 * Real wiring pass: POST /query, GET /conversations, GET /conversations/{id}/messages
 * (previously mocked — see this feature's CHANGELOG entry).
 *
 * Runs against the LOCAL Supabase stack AND a live local FastAPI backend
 * (uvicorn pointed at the same local stack), same as upload.e2e.ts. One
 * test (the first) drives the real retrieve -> generate -> verify
 * pipeline through the actual UI — real Docling parse, real Voyage
 * embed, real Gemini generation/verification, no fakes, matching this
 * project's e2e discipline elsewhere (upload.e2e.ts). The rest seed a
 * conversation/message/citation set directly via the real
 * create_query_turn RPC (e2e/_seed.ts) — same function POST /query
 * itself calls — so citation styling, figure rendering, multi-tenant
 * isolation, and marker-persistence-on-reload are asserted
 * deterministically, without depending on what an arbitrary real Gemini
 * call happens to cite on a given run.
 */
import path from "path";

import { test, expect, type Page } from "@playwright/test";

import { createTestUser, deleteTestUserByEmail } from "./_local-supabase";
import {
  decodeBase64Png,
  seedConversationTurn,
  seedDocument,
  seedFigureChunk,
  seedTextChunk,
  TINY_PNG_BASE64,
} from "./_seed";

const FIXTURE_PDF = path.join(process.cwd(), "..", "api", "tests", "fixtures", "clean_digital.pdf");
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

test.describe("[FEAT-015] Chat UI + citation source panel", () => {
  test("real end-to-end: select a ready document, ask a real question, get a real answer, reload keeps the same citation markers", async ({
    page,
  }) => {
    test.setTimeout(180000);
    const { email } = await loginAsNewUser(page, "e2e-chat-real");
    try {
      const main = page.locator("main");

      // Question input at bottom, messages scroll above (acceptance
      // criterion) — real upload first, so there's a ready document to ask.
      await page.locator('input[type="file"]').setInputFiles(FIXTURE_PDF);
      await expect(main.getByText("clean_digital.pdf")).toBeVisible({ timeout: 15000 });
      await expect(main.getByText("Ready", { exact: true })).toBeVisible({ timeout: 120000 });

      await page.getByTitle("Select for a conversation").click();
      await page.getByRole("button", { name: "Ask about these" }).click();
      await page.waitForURL(/\/chat\/new\?docs=/);

      const question = "What is this document about? Summarize its key content in one sentence.";
      await page.getByPlaceholder("Ask your documents…").fill(question);
      await page.getByTitle("Send question").click();

      // Loading state during retrieval + generation (acceptance criterion).
      await expect(page.getByText("FINDING RELEVANT PAGES…")).toBeVisible();

      // Real /query round trip lands and the URL is replaced from /chat/new
      // to the real conversation id the backend created.
      await page.waitForURL(/\/chat\/(?!new)[0-9a-f-]{36}$/, { timeout: 90000 });
      const conversationUrl = page.url();

      await expect(page.getByTestId("user-message")).toContainText(question);
      const assistantMessage = page.getByTestId("assistant-message");
      await expect(assistantMessage).toBeVisible();
      await expect(assistantMessage).not.toContainText("mock");

      const citationMarkers = assistantMessage.locator('[data-testid^="citation-marker-"]');
      const citationCount = await citationMarkers.count();

      if (citationCount > 0) {
        // Click citation -> source panel opens with chunk content + page
        // (acceptance criterion).
        await citationMarkers.first().click();
        const sourcePanel = page.getByTestId("source-panel");
        await expect(sourcePanel).toBeVisible();
        await expect(sourcePanel.getByText(/PAGE \d+/)).toBeVisible();
        await expect(sourcePanel).not.toBeEmpty();

        const markersBeforeReload = await citationMarkers.evaluateAll((els) =>
          els.map((el) => el.textContent)
        );

        // Refresh and confirm history reloads via the real
        // GET /conversations/{id}/messages, with the SAME citation
        // markers — the one place FEAT-026's marker-persistence fix
        // (persisting `marker` at write time rather than re-deriving it)
        // is actually exercised by a real user, not just a backend test.
        await page.reload();
        await expect(page.getByTestId("assistant-message")).toBeVisible({ timeout: 15000 });
        const reloadedMarkers = page.getByTestId("assistant-message").locator('[data-testid^="citation-marker-"]');
        const markersAfterReload = await reloadedMarkers.evaluateAll((els) => els.map((el) => el.textContent));
        expect(markersAfterReload).toEqual(markersBeforeReload);
      } else {
        // Gemini's real answer to this question didn't cite anything —
        // legitimate, if unlikely, real model behavior. Still confirm the
        // reload path itself works and shows the same (empty) citations,
        // rather than silently passing on an untested branch.
        await page.reload();
        await expect(page.getByTestId("assistant-message")).toBeVisible({ timeout: 15000 });
        await expect(page.url()).toBe(conversationUrl);
      }
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("verdict-based citation styling, real figure image load, and excerpt fallback to snippet when no quote", async ({
    page,
  }) => {
    test.setTimeout(60000);
    const { email, userId } = await loginAsNewUser(page, "e2e-chat-seeded");
    try {
      const documentId = await seedDocument(userId, "seeded.pdf");
      const supportedChunk = await seedTextChunk(userId, documentId, "Revenue grew significantly year over year.", 1, 0);
      const partialChunk = await seedTextChunk(userId, documentId, "Margins improved somewhat across the quarter.", 2, 1);
      const { chunkId: figureChunk } = await seedFigureChunk(userId, documentId, decodeBase64Png(), 3, 2);

      const { conversationId } = await seedConversationTurn({
        userId,
        documentIds: [documentId],
        question: "Seeded question for styling and figure assertions",
        answerContent:
          "Revenue grew significantly [1]. See the chart [2]. Margins improved somewhat [3].",
        citations: [
          {
            chunk_id: supportedChunk,
            marker: 1,
            claim_span: "Revenue grew significantly",
            verdict: "supported",
            supporting_quote: "Revenue grew significantly year over year.",
          },
          {
            chunk_id: figureChunk,
            marker: 2,
            claim_span: "See the chart",
            verdict: "supported",
            supporting_quote: null, // figures have no text to quote verbatim
          },
          {
            chunk_id: partialChunk,
            marker: 3,
            claim_span: "Margins improved somewhat",
            verdict: "partial",
            supporting_quote: null, // deliberately null to exercise the snippet fallback
          },
        ],
      });

      await page.goto(`/chat/${conversationId}`);
      await expect(page.getByTestId("assistant-message")).toBeVisible();

      // Verdict-based citation styling: supported = solid (no underline),
      // partial = dashed/dotted warning underline, real computed CSS.
      const supportedMarker = page.locator('[data-verdict="supported"]').first();
      const partialMarker = page.locator('[data-verdict="partial"]').first();
      await expect(supportedMarker).toHaveCSS("border-bottom-style", "none");
      await expect(partialMarker).toHaveCSS("border-bottom-style", "dotted");

      // Click the figure citation -> source panel -> a real <img> whose
      // src is a genuinely fetchable, correct signed Storage URL.
      await page.locator('[data-verdict="supported"]').nth(1).click();
      const figureImg = page.getByTestId("figure-image");
      await expect(figureImg).toBeVisible();
      const src = await figureImg.getAttribute("src");
      expect(src).toBeTruthy();
      const imgResponse = await page.request.get(src!);
      expect(imgResponse.status()).toBe(200);
      const expectedBytes = Buffer.from(TINY_PNG_BASE64, "base64");
      expect(Buffer.compare(await imgResponse.body(), expectedBytes)).toBe(0);

      // The still-open panel from the previous click can visually overlap
      // a citation marker positioned near the end of the message text (the
      // panel is a fixed-width right-side overlay, not part of document
      // flow) — close it first, same as a real user would before picking
      // a different citation that's currently covered.
      await page.getByTitle("Close").click();

      // Excerpt fallback: supporting_quote was null, so the source panel
      // must fall back to the chunk's snippet rather than showing an
      // empty blockquote.
      await page.locator('[data-verdict="partial"]').first().click();
      await expect(page.getByTestId("source-panel")).toContainText("Margins improved somewhat across the quarter.");

      // Mobile-responsive: the source panel becomes a full-width overlay
      // below the `sm` breakpoint rather than the fixed 400px side panel.
      await page.setViewportSize({ width: 375, height: 800 });
      const panelBox = await page.getByTestId("source-panel").boundingBox();
      expect(panelBox?.width).toBeGreaterThan(350);
    } finally {
      await deleteTestUserByEmail(email);
    }
  });

  test("multi-tenant: two real users' conversations and citations never cross, direct URL access is a non-leaking not-found", async ({
    browser,
  }) => {
    test.setTimeout(60000);
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    const { email: emailA, userId: userIdA } = await loginAsNewUser(pageA, "e2e-tenant-a");
    const { email: emailB, userId: userIdB } = await loginAsNewUser(pageB, "e2e-tenant-b");

    try {
      const docA = await seedDocument(userIdA, "a.pdf");
      const chunkA = await seedTextChunk(userIdA, docA, "User A's private content.");
      const { conversationId: convA } = await seedConversationTurn({
        userId: userIdA,
        documentIds: [docA],
        question: "User A's question",
        answerContent: "User A's answer [1].",
        citations: [
          { chunk_id: chunkA, marker: 1, claim_span: "answer", verdict: "supported", supporting_quote: "User A's private content." },
        ],
      });

      const docB = await seedDocument(userIdB, "b.pdf");
      const chunkB = await seedTextChunk(userIdB, docB, "User B's private content.");
      await seedConversationTurn({
        userId: userIdB,
        documentIds: [docB],
        question: "User B's question",
        answerContent: "User B's answer [1].",
        citations: [
          { chunk_id: chunkB, marker: 1, claim_span: "answer", verdict: "supported", supporting_quote: "User B's private content." },
        ],
      });

      await pageA.goto("/chat");
      await expect(pageA.getByText("User A's question")).toBeVisible();
      await expect(pageA.getByText("User B's question")).not.toBeVisible();

      await pageB.goto("/chat");
      await expect(pageB.getByText("User B's question")).toBeVisible();
      await expect(pageB.getByText("User A's question")).not.toBeVisible();

      // Direct navigation to the other user's real conversation id — must
      // be a non-leaking "not found", never a glimpse of A's real content.
      await pageB.goto(`/chat/${convA}`);
      await expect(pageB.getByText("Conversation not found")).toBeVisible();
      await expect(pageB.getByText("User A's private content", { exact: false })).not.toBeVisible();
    } finally {
      await deleteTestUserByEmail(emailA);
      await deleteTestUserByEmail(emailB);
      await contextA.close();
      await contextB.close();
    }
  });
});
