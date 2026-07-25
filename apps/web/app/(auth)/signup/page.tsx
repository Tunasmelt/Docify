"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/browser";

export default function SignupPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [info, setInfo] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  async function handleSignUp(e: React.SyntheticEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }

    setLoading(true);
    const { data, error } = await supabase.auth.signUp({ email, password });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }

    if (data.session) {
      router.push("/documents");
      router.refresh();
    } else {
      setInfo("Check your email to confirm your account before signing in.");
    }
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
            Your documents,
            <br />
            on the <em className="italic text-[#8FC5A8]">record</em>.
            <sup className="text-2xl text-[#4C9B75]">1</sup>
          </h1>
          <p className="m-0 max-w-[400px] text-base leading-relaxed text-[#B8AF9F]">
            Set up a workspace for your team in under a minute. Your first
            PDF is one drag away.
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
            Create your account
          </h2>
          <p className="m-0 mb-8 text-sm text-muted">
            Start a workspace, invite your team later.
          </p>
          <form className="flex flex-col gap-[18px]" onSubmit={handleSignUp}>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="signup-email"
                className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
              >
                Email
              </label>
              <Input
                id="signup-email"
                type="email"
                placeholder="you@firm.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="signup-password"
                className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
              >
                Password
              </label>
              <Input
                id="signup-password"
                type="password"
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="signup-confirm"
                className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
              >
                Confirm password
              </label>
              <Input
                id="signup-confirm"
                type="password"
                placeholder="Repeat your password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
              />
            </div>
            {error ? <p className="m-0 text-xs text-red-500">{error}</p> : null}
            {info ? <p className="m-0 text-xs text-accent">{info}</p> : null}
            <Button type="submit" size="lg" className="mt-1" disabled={loading}>
              {loading ? "Creating account…" : "Create account"}
            </Button>
          </form>
          <p className="mt-4 text-[12px] leading-relaxed text-faint">
            By continuing you agree to the{" "}
            <a href="#" className="text-muted underline">
              Terms
            </a>{" "}
            and{" "}
            <a href="#" className="text-muted underline">
              Privacy Policy
            </a>
            .
          </p>
          <p className="mt-6 text-center text-sm text-muted">
            Already have an account? <Link href="/login">Sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
