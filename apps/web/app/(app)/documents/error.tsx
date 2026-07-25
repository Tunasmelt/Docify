"use client";

import { ErrorFallback } from "@/components/error-fallback";

export default function DocumentsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <ErrorFallback
      error={error}
      reset={reset}
      title="Something went wrong loading your documents"
      description="This is on our end, not yours — try again, or come back in a moment."
    />
  );
}
