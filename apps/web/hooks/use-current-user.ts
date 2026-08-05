"use client";

import * as React from "react";
import type { User } from "@supabase/supabase-js";

import { createClient } from "@/lib/supabase/browser";

export interface CurrentUser {
  id: string;
  email: string;
  /** user_metadata.display_name if set (Settings, batch 1 item 1) —
   * falls back to the email's local-part so every page that renders
   * identity (sidebar, Settings) has a real name to show even before a
   * user ever sets one, rather than an empty string. */
  name: string;
  /** Two-letter initials derived from `name` — same computation
   * previously hardcoded per-page ("AK" for "Ana Kovač"), now real. */
  initials: string;
  /** user_metadata.avatar_url (Settings, batch 1 item 2) — a public URL
   * (the avatars bucket is public-read, see migrations/…_avatars.sql),
   * so this can be used directly in an <img src> with no signed-URL
   * refresh needed. Undefined until a user uploads one. */
  avatarUrl?: string;
}

function deriveName(user: User): string {
  const displayName = user.user_metadata?.display_name;
  if (typeof displayName === "string" && displayName.trim()) return displayName.trim();
  return user.email?.split("@")[0] ?? "there";
}

function deriveInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function toCurrentUser(user: User): CurrentUser {
  const name = deriveName(user);
  const avatarUrl = typeof user.user_metadata?.avatar_url === "string" ? user.user_metadata.avatar_url : undefined;
  return { id: user.id, email: user.email ?? "", name, initials: deriveInitials(name), avatarUrl };
}

/** Real logged-in user identity — replaces the hardcoded `const USER =
 * { initials: "AK", name: "Ana Kovač", email: "ana@firm.com" }` that
 * used to be independently copy-pasted into documents/page.tsx,
 * chat/page.tsx, and chat/[conversation_id]/page.tsx (three identical
 * fakes, confirmed via a repo-wide grep before this hook existed). One
 * shared source now, so a display-name/avatar change in Settings shows
 * up identically everywhere identity is rendered, with zero risk of one
 * page silently drifting from another.
 *
 * Subscribes to onAuthStateChange (not just a one-time getUser() call)
 * so a same-tab navigation back from Settings after a profile update
 * reflects the change immediately — Supabase's own updateUser() already
 * fires a USER_UPDATED auth event with the fresh user object, no manual
 * refetch/refresh needed. */
export function useCurrentUser(): CurrentUser | null {
  const supabase = React.useMemo(() => createClient(), []);
  const [user, setUser] = React.useState<CurrentUser | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    supabase.auth.getUser().then(({ data }) => {
      if (!cancelled && data.user) setUser(toCurrentUser(data.user));
    });
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      if (cancelled) return;
      setUser(session?.user ? toCurrentUser(session.user) : null);
    });
    return () => {
      cancelled = true;
      subscription.subscription.unsubscribe();
    };
  }, [supabase]);

  return user;
}
