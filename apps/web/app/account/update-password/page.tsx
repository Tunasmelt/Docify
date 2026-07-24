"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/browser";

export default function UpdatePasswordPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  async function handleSubmit(e: React.SyntheticEvent) {
    e.preventDefault();
    setError(null);

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }

    setLoading(true);
    const { error } = await supabase.auth.updateUser({ password });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }

    router.push("/documents");
    router.refresh();
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
            Set a new
            <br />
            <em className="italic text-[#8FC5A8]">password</em>.<sup className="text-2xl text-[#4C9B75]">1</sup>
          </h1>
          <p className="m-0 max-w-[400px] text-base leading-relaxed text-[#B8AF9F]">
            Choose something you haven't used before. You'll be signed
            straight back into your workspace.
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
            Choose a new password
          </h2>
          <p className="m-0 mb-8 text-sm text-muted">
            Enter and confirm your new password below.
          </p>
          <form className="flex flex-col gap-[18px]" onSubmit={handleSubmit}>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="new-password"
                className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
              >
                New password
              </label>
              <Input
                id="new-password"
                type="password"
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="confirm-password"
                className="text-xs font-semibold uppercase tracking-[0.06em] text-muted"
              >
                Confirm new password
              </label>
              <Input
                id="confirm-password"
                type="password"
                placeholder="Repeat your new password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
              />
            </div>
            {error ? <p className="m-0 text-xs text-red-500">{error}</p> : null}
            <Button type="submit" size="lg" className="mt-1" disabled={loading}>
              {loading ? "Updating…" : "Update password"}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
