import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const AUTH_PATHS = ["/login", "/signup"];

// The landing page ("/") — public like AUTH_PATHS (an unauthenticated
// visitor must see it, not bounce to /login), but also redirects an
// authenticated visitor onward like AUTH_PATHS do, straight to
// /documents rather than showing marketing copy to someone already
// signed in. Exact-match only: pathname.startsWith("/") would match
// every route in the app, unlike AUTH_PATHS's prefix check.
const LANDING_PATH = "/";

// The PKCE code-exchange route (OAuth + password recovery) establishes auth
// state itself — it must never be bounced by either redirect branch below,
// since at the point middleware runs, that state doesn't exist yet.
const EXEMPT_PREFIXES = ["/auth/"];

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (EXEMPT_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return NextResponse.next();
  }

  let response = NextResponse.next({
    request: {
      headers: request.headers,
    },
  });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  const { data } = await supabase.auth.getClaims();
  const isAuthenticated = data !== null;
  const isAuthPath = AUTH_PATHS.some((path) => pathname.startsWith(path));
  const isLandingPath = pathname === LANDING_PATH;

  if (!isAuthenticated && !isAuthPath && !isLandingPath) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    const redirectResponse = NextResponse.redirect(url);
    response.cookies.getAll().forEach((cookie) => redirectResponse.cookies.set(cookie));
    return redirectResponse;
  }

  if (isAuthenticated && (isAuthPath || isLandingPath)) {
    const url = request.nextUrl.clone();
    url.pathname = "/documents";
    const redirectResponse = NextResponse.redirect(url);
    response.cookies.getAll().forEach((cookie) => redirectResponse.cookies.set(cookie));
    return redirectResponse;
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
