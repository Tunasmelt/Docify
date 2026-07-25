"use client";

import { ErrorFallback } from "@/components/error-fallback";

export default function ConversationError({
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
      title="Something went wrong loading this conversation"
      description="This is on our end, not yours — try again, or head back to your conversations."
      backHref="/chat"
      backLabel="Back to Conversations"
    />
  );
}
