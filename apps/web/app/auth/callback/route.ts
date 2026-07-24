import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

/** The Next.js dev server normalizes `new URL(request.url).origin` to
 * "localhost" regardless of which host the client actually connected
 * with (confirmed live: a request to 127.0.0.1:3000 still reports
 * "localhost:3000" as its origin) — redirecting there would land the
 * browser on a different origin than the one holding the session cookie
 * `exchangeCodeForSession` just set, breaking the flow. The real `Host`
 * header (or `x-forwarded-host` behind a proxy) reflects what the browser
 * will actually honor for cookies, so it's what building the redirect
 * target must use instead. */
function resolveOrigin(request: Request, fallbackOrigin: string): string {
  const forwardedHost = request.headers.get("x-forwarded-host");
  const forwardedProto = request.headers.get("x-forwarded-proto");
  const host = forwardedHost ?? request.headers.get("host");
  if (!host) return fallbackOrigin;
  const protocol = forwardedProto ?? (process.env.NODE_ENV === "development" ? "http" : "https");
  return `${protocol}://${host}`;
}

export async function GET(request: Request) {
  const { searchParams, origin: fallbackOrigin } = new URL(request.url);
  const origin = resolveOrigin(request, fallbackOrigin);
  const code = searchParams.get("code");
  let next = searchParams.get("next") ?? "/documents";
  if (!next.startsWith("/")) next = "/documents";

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
    return NextResponse.redirect(
      `${origin}/login?error=${encodeURIComponent(error.message)}`
    );
  }

  // GoTrue redirects here directly on failure too (expired/reused/invalid
  // link) — no code, but its own error params are attached instead.
  const errorDescription = searchParams.get("error_description");
  const loginUrl = new URL(`${origin}/login`);
  if (errorDescription) {
    loginUrl.searchParams.set("error", errorDescription);
  } else {
    loginUrl.searchParams.set("error", "This link is invalid or has expired.");
  }
  return NextResponse.redirect(loginUrl);
}
