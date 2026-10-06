// Browser-side Sentry. Sentry's webpack plugin injects this file into the
// client bundle (Next 14; Next 15.3+ also loads it natively).
import * as Sentry from "@sentry/nextjs";

import { sentryOptions } from "@/lib/sentry-options";

Sentry.init(sentryOptions());
