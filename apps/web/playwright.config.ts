import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 30_000,
  fullyParallel: false,
  // These tests hit a real local Supabase stack + real Next.js dev-mode
  // compilation, not mocks — concurrent workers across files contend for
  // the same dev server and caused a real timeout (not a logic bug) when
  // this repo had 2+ e2e files running in parallel. Single worker trades
  // some wall-clock time for determinism, matching this project's
  // real-infrastructure testing discipline elsewhere.
  workers: 1,
  reporter: "list",
  use: {
    // Must match Supabase's local `site_url` (config.toml) exactly —
    // GoTrue validates `redirectTo` against `site_url`/`additional_redirect_urls`
    // and silently falls back to bare `site_url` (dropping the path) for any
    // origin it doesn't recognize. "localhost" and "127.0.0.1" are different
    // origins to that check even though they resolve to the same server —
    // confirmed live when this project's own password-reset redirectTo was
    // silently dropped for exactly this reason.
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
