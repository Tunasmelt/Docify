"use client";

import * as React from "react";
import * as Sentry from "@sentry/nextjs";

/** Last-resort boundary for errors in the root layout itself, which the
 * per-route error.tsx files can't catch. It replaces the root layout, so it
 * renders its own <html>/<body> and stays deliberately plain. */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  React.useEffect(() => {
    // eslint-disable-next-line no-console -- STANDARDS.md: technical detail goes to console.error
    console.error(error);
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", textAlign: "center", padding: "20vh 24px" }}>
        <h1 style={{ fontWeight: 500 }}>Something went wrong</h1>
        <p>This is on our end, not yours. Try again, or come back in a moment.</p>
        <button type="button" onClick={() => reset()} style={{ marginTop: 16, padding: "8px 16px" }}>
          Try again
        </button>
      </body>
    </html>
  );
}
