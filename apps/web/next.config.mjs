import { withSentryConfig } from "@sentry/nextjs/config";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Next 14 only runs instrumentation.ts (server/edge Sentry) with this on.
  experimental: { instrumentationHook: true },
};

export default withSentryConfig(nextConfig, {
  // Source maps are uploaded only when these are set (Vercel env vars), so
  // builds without a Sentry account behave exactly as before.
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  sourcemaps: { disable: !process.env.SENTRY_AUTH_TOKEN },
  silent: !process.env.CI,
  telemetry: false,
  // Tracing and replay are off (lib/sentry-options.ts), so drop their code.
  webpack: { treeshake: { removeDebugLogging: true, removeTracing: true } },
});
