/**
 * Shared Sentry options for the browser, server and edge runtimes. Sentry
 * stays off unless a DSN is set (NEXT_PUBLIC_SENTRY_DSN, inlined at build
 * time), so local dev and preview builds without it send nothing.
 *
 * Privacy: users' questions, answers and document text pass through this
 * app, so default PII, session replay and performance tracing are all off.
 * Only errors are reported, which also keeps usage inside the free tier.
 */
export function sentryOptions() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  return {
    dsn,
    enabled: Boolean(dsn),
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || process.env.NODE_ENV,
    sendDefaultPii: false,
    tracesSampleRate: 0,
  };
}
