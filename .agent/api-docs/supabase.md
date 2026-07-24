# Supabase — Next.js App Router SSR Auth

Verified: 2026-07-24
Installed/pinned: @supabase/ssr@0.12.3, @supabase/supabase-js@2.110.8

## Client setup (per official `/docs/guides/auth/server-side/nextjs` +
`/docs/guides/auth/server-side/creating-a-client`, fetched live today)

- **Browser client** (client components): `createBrowserClient(url, key)` from `@supabase/ssr`.
- **Server client** (server components / route handlers / middleware): `createServerClient(url, key, { cookies: { getAll, setAll } })`.
  - `getAll()` reads cookies from the current context (`next/headers` `cookies()` in server
    components, `request.cookies` in middleware).
  - `setAll()` writes cookies back (`cookieStore.set(...)` in server components — wrap in
    try/catch since Server Components can't set cookies, only Server Actions/Route Handlers can;
    `response.cookies.set(...)` in middleware).
- **Middleware**: create a `NextResponse.next()`, build a server client against
  `request.cookies`/`response.cookies`, then call `supabase.auth.getClaims()` — this is the
  official current guidance ("Always use `supabase.auth.getClaims()` to protect pages and user
  data... Never trust `getSession()` inside server code" — it isn't guaranteed to revalidate).
  `getClaims()` locally verifies the JWT signature (asymmetric ES256 via JWKS) on every call,
  which is what makes it safe to use for authorization decisions — this lines up exactly with
  this project's FEAT-003 backend middleware, which independently verifies the same ES256/JWKS
  token via `PyJWKClient`.

## Env var naming — decision

Official current docs use `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (Supabase has moved to
"publishable key" terminology alongside the asymmetric JWT signing-key rollout). This repo
already has `NEXT_PUBLIC_SUPABASE_ANON_KEY` wired through `.env.example`/`.env.local`. Both the
legacy anon JWT and the new publishable key work identically as the client's `apikey` header —
the API-key format used to call the gateway is independent of the ES256 access-token format
issued on login. Decision: keep the existing `NEXT_PUBLIC_SUPABASE_ANON_KEY` name rather than
rename an established, already-wired convention — not a functional requirement, no behavior
difference either way.

## Gotcha carried into middleware.ts

Per docs: do not run any logic between `createServerClient(...)` and `getClaims()` — and always
return the same `response` object whose cookies were mutated by `setAll` (creating a fresh
`NextResponse.next()`/`.redirect()` after the fact drops the refreshed session cookies).

**2026-07-24, confirmed via live self-audit: this project's own `middleware.ts` violated this
gotcha in both its redirect branches** (`NextResponse.redirect(url)` built fresh, never receiving
`setAll`'s cookies). Official docs troubleshooting page (fetched live) states the fix: *"If you're
creating a new response object... make sure to copy over the cookies"* — the fetched summary's
exact quoted method name (`myNewResponse.cookies.setAll(...)`) turned out NOT to exist on Next.js's
real `ResponseCookies` type (`tsc` caught it: `Property 'setAll' does not exist on type
'ResponseCookies'. Did you mean 'getAll'?` — `next/server`'s cookies object only has
`get`/`getAll`/`set`/`delete`, no bulk setter). Ground truth from the type system, not the fetch
summary: iterate manually — `response.cookies.getAll().forEach((cookie) =>
redirectResponse.cookies.set(cookie))`. Applied in both redirect branches in `middleware.ts`.
Worth remembering: a fetched doc *summary* can misquote an API even when it's paraphrasing a real
page correctly in spirit — the compiler is the actual ground truth for exact method names, same
discipline as everywhere else in this project (verify empirically, don't trust the first source
verbatim).

## PKCE code-exchange callback (`app/auth/callback/route.ts`)

Exact current recommended Next.js App Router callback code (fetched live from
`/docs/guides/auth/social-login/auth-google`, the same PKCE contract also applies to
`resetPasswordForEmail` recovery links — both are `flowType: "pkce"` under `@supabase/ssr`,
confirmed via the installed `createBrowserClient.js`/`createServerClient.js` source):

```ts
import { NextResponse } from 'next/server'
import { createClient } from '@/utils/supabase/server'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  let next = searchParams.get('next') ?? '/'
  if (!next.startsWith('/')) next = '/'

  if (code) {
    const supabase = await createClient()
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      const forwardedHost = request.headers.get('x-forwarded-host')
      const isLocalEnv = process.env.NODE_ENV === 'development'
      if (isLocalEnv) return NextResponse.redirect(`${origin}${next}`)
      else if (forwardedHost) return NextResponse.redirect(`https://${forwardedHost}${next}`)
      else return NextResponse.redirect(`${origin}${next}`)
    }
  }
  return NextResponse.redirect(`${origin}/auth/auth-code-error`)
}
```

Docify's version (below) reuses this exactly, with two adaptations: the error path redirects to
`/login?error=...` (forwarding GoTrue's own `error_description` when present, e.g. `otp_expired`)
instead of a dedicated error page, since `/login` already needed real error-state handling; and
`next` defaults to `/documents` — recovery links pass `next=/account/update-password` explicitly
via `resetPasswordForEmail`'s `redirectTo` option, which is how this one route serves both the
OAuth and password-recovery flows without needing to branch on `type`.

**2026-07-24, real bug found live in the official pattern's own `isLocalEnv` branch:**
`new URL(request.url).origin` inside a Next.js (14.2.35) dev-server Route Handler resolves to
`http://localhost:3000` **regardless of which host the client actually connected with** —
confirmed via direct `curl http://127.0.0.1:3000/auth/callback?...` returning a `Location:
http://localhost:3000/...` redirect. Since GoTrue's own redirect-URL allowlist (`site_url`/
`additional_redirect_urls` in `config.toml`) is pinned to `127.0.0.1:3000`, and the session
cookie `exchangeCodeForSession` sets is scoped to whatever origin the request came in on, this
mismatch silently broke the entire flow: browser lands on `localhost:3000`, which doesn't hold
the cookie set for `127.0.0.1:3000`, middleware sees no session, bounces to `/login`. The
official example's `isLocalEnv ? origin : ...` branch does not account for this — it assumes
`request.url`'s own origin is trustworthy in dev, which it isn't here. Fixed by preferring the
real `Host`/`x-forwarded-host` request header (what the browser will actually honor for cookies)
over `new URL(request.url).origin` unconditionally, not just in the non-local branches — see
`resolveOrigin()` in `app/auth/callback/route.ts`. Also required aligning
`playwright.config.ts`'s `baseURL`/`webServer.url` from `localhost:3000` to `127.0.0.1:3000` to
match `site_url` — Playwright's default `localhost` would have hit the identical bug from the
test side even with the route fixed.
