"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { ProfileError, signOutOtherSessions } from "@/lib/supabase/profile";

/** Part 2 — password change LINKS to the existing /account/update-
 * password page (FEAT-013) rather than moving it under /settings.
 * Reasoning: that page is also the landing destination for the
 * password-RECOVERY flow — a user arriving fresh from an emailed
 * reset link, not yet "in" the app shell at all (no sidebar context,
 * standalone auth-style layout matching login/signup). Moving it under
 * /settings would conflate two different entry contexts; a link keeps
 * the one existing implementation serving both without a second one.
 *
 * Session management: investigated first (lib/supabase/profile.ts's
 * signOutOtherSessions docstring) — the installed SDK has no session/
 * device list at all, client or admin. signOut({ scope: 'others' }) is
 * the real, honest capability: revokes every OTHER session's refresh
 * token, keeps the current one signed in. */
export function SecuritySection() {
  const router = useRouter();
  const [signingOutOthers, setSigningOutOthers] = React.useState(false);
  const [othersSignedOut, setOthersSignedOut] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function handleSignOutOthers() {
    setError(null);
    setSigningOutOthers(true);
    try {
      await signOutOtherSessions();
      setOthersSignedOut(true);
    } catch (err) {
      setError(err instanceof ProfileError ? err.message : "Couldn't sign out other sessions. Try again.");
    } finally {
      setSigningOutOthers(false);
    }
  }

  return (
    <SettingsSection title="Security">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="m-0 text-[14px] font-medium">Password</p>
          <p className="m-0 mt-0.5 text-[13px] text-faint">Change the password you sign in with.</p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => router.push("/account/update-password")}>
          Change password
        </Button>
      </div>

      <div className="border-t border-line pt-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="m-0 text-[14px] font-medium">Other sessions</p>
            <p className="m-0 mt-0.5 max-w-[420px] text-[13px] leading-relaxed text-faint">
              Revoke access everywhere else you&apos;re logged in — this browser stays signed in. Other sessions
              stop working the next time they&apos;d need to refresh, not necessarily this instant. A device list
              isn&apos;t something Supabase Auth exposes, but this covers the same real need if you think your
              account was accessed somewhere you don&apos;t recognize.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={signingOutOthers}
            onClick={handleSignOutOthers}
          >
            {othersSignedOut ? "Done" : signingOutOthers ? "Signing out…" : "Sign out other sessions"}
          </Button>
        </div>
        {error ? <p className="m-0 mt-2 text-[13px] text-destructive">{error}</p> : null}
      </div>
    </SettingsSection>
  );
}
