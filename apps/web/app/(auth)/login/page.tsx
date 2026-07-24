"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/browser";

function GoogleIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 48 48">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.3 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z" />
      <path fill="#FBBC05" d="M10.4 28.7a14.5 14.5 0 0 1 0-9.4l-7.8-6.1a24 24 0 0 0 0 21.6l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.2 0 11.4-2 15.4-5.5l-7.5-5.8c-2.1 1.4-4.8 2.3-7.9 2.3-6.3 0-11.7-3.8-13.6-9.3l-7.8 6.1C6.5 42.6 14.6 48 24 48z" />
    </svg>
  );
}

const SSO_ENABLED = true;

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = React.useMemo(() => createClient(), []);
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    const paramError = searchParams.get("error");
    if (paramError) {
      setError(paramError);
    } else if (searchParams.get("code")) {
      // A code that reached /login directly (never went through
      // /auth/callback's exchange) is stale — an old bookmarked link, or a
      // link issued before this route existed. Never render a bare sign-in
      // form as if nothing happened; the user needs a fresh link.
      setError("This link is no longer valid. Request a new one and try again.");
    }
  }, [searchParams]);

  async function handleSignIn(e: React.SyntheticEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }
    router.push("/documents");
    router.refresh();
  }

  async function handleGoogleSignIn(e: React.SyntheticEvent) {
    e.preventDefault();
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback?next=/documents` },
    });
    if (error) setError(error.message);
  }

  async function handleForgotPassword(e: React.SyntheticEvent) {
    e.preventDefault();
    if (!email) {
      setError("Enter your email above first, then click Forgot.");
      return;
    }
    setError(null);
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback?next=/account/update-password`,
    });
    setError(error ? error.message : "Password reset email sent — check your inbox.");
  }

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-2">
      <div
        className="hidden flex-col justify-between p-14 text-[#F4EFE6] md:flex"
        style={{
          background:
            "#1B1815 radial-gradient(ellipse 80% 60% at 20% 0%, rgba(76,155,117,0.10), transparent)",
        }}
      >
        <div className="font-serif text-2xl font-semibold">
          Docify
          <sup className="text-sm font-medium text-[#4C9B75]">1</sup>
        </div>
        <div className="animate-fade-up">
          <h1 className="m-0 mb-5 text-balance font-serif text-[52px] font-medium leading-[1.12] tracking-[-0.01em]">
            Ask your documents.
            <br />
            Get answers <em className="italic text-[#8FC5A8]">with receipts</em>.
            <sup className="text-2xl text-[#4C9B75]">1</sup>
          </h1>
          <p className="m-0 max-w-[400px] text-base leading-relaxed text-[#B8AF9F]">
            Upload PDFs, ask questions in plain language, and follow every
            answer back to the page it came from.
          </p>
        </div>
        <div>
          <div className="mb-4 h-px bg-[rgba(244,239,230,0.15)]" />
          <p className="m-0 font-mono text-xs text-[#8A8171]">
            <span className="text-[#4C9B75]">1</span>
            &nbsp; Every answer cites the exact source page. Verified, not
            guessed.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-center px-10 py-14">
        <div className="w-full max-w-[360px]">
          <h2 className="m-0 mb-1.5 font-serif text-[32px] font-medium">
            Welcome back
          </h2>
          <p className="m-0 mb-8 text-sm text-muted">
            Sign in to your workspace.
          </p>
          <form className="flex flex-col gap-[18px]" onSubmit={handleSignIn}>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="login-email"
                className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
              >
                Email
              </label>
              <Input
                id="login-email"
                type="email"
                placeholder="you@firm.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between">
                <label
                  htmlFor="login-password"
                  className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
                >
                  Password
                </label>
                <a href="#" onClick={handleForgotPassword} className="text-xs">
                  Forgot?
                </a>
              </div>
              <Input
                id="login-password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error ? <p className="m-0 text-xs text-red-500">{error}</p> : null}
            <Button type="submit" size="lg" className="mt-1" disabled={loading}>
              {loading ? "Signing in…" : "Sign in"}
            </Button>
            {SSO_ENABLED ? (
              <>
                <div className="flex items-center gap-3 text-xs text-faint">
                  <span className="h-px flex-1 bg-line" />
                  or
                  <span className="h-px flex-1 bg-line" />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="lg"
                  onClick={handleGoogleSignIn}
                  className="gap-2.5 font-medium"
                >
                  <GoogleIcon />
                  Continue with Google
                </Button>
              </>
            ) : null}
          </form>
          <p className="mt-7 text-center text-sm text-muted">
            New to Docify?{" "}
            <Link href="/signup">Create an account</Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <React.Suspense fallback={null}>
      <LoginForm />
    </React.Suspense>
  );
}
