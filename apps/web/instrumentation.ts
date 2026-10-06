// Loads the server/edge Sentry config for whichever runtime is starting.
// Next 14 needs `experimental.instrumentationHook` (next.config.mjs).
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}
