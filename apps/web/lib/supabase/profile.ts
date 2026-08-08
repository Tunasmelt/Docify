import { createClient } from "@/lib/supabase/browser";

const AVATAR_MAX_BYTES = 5 * 1024 * 1024; // 5 MB
const AVATAR_ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);

export class ProfileError extends Error {}

/** display_name lives in user_metadata (auth.users, Supabase-managed) —
 * no schema change, no new table, matching this batch's own framing
 * ("Profile and security both sit directly on Supabase Auth's existing
 * capabilities"). updateUser() fires a USER_UPDATED auth event on
 * success, which useCurrentUser()'s onAuthStateChange subscription
 * picks up automatically — no manual refetch needed by callers. */
export async function updateDisplayName(name: string): Promise<void> {
  const trimmed = name.trim();
  if (!trimmed) throw new ProfileError("Display name must not be empty.");
  const supabase = createClient();
  const { error } = await supabase.auth.updateUser({ data: { display_name: trimmed } });
  if (error) throw new ProfileError(error.message);
}

export function validateAvatarFile(file: File): string | null {
  if (!AVATAR_ALLOWED_TYPES.has(file.type)) {
    return "Please choose a JPEG, PNG, WebP, or GIF image.";
  }
  if (file.size > AVATAR_MAX_BYTES) {
    return "Image must be under 5 MB.";
  }
  return null;
}

/** Uploads to the public `avatars` bucket at a FIXED path per user
 * (`{user_id}/avatar`, extension-less) — `upsert: true` replaces
 * whatever was there before rather than accumulating orphaned old
 * files, matching a profile picture's real "one current avatar"
 * semantics (see migrations/20260804_001_avatars_bucket.sql's own
 * comment). Returns the real public URL, then immediately persists it
 * to user_metadata.avatar_url via updateUser() — the two-step sequence
 * matters: if the Storage upload fails, nothing about the user's
 * metadata (and therefore what every page renders) has changed yet. */
export async function uploadAvatar(file: File, userId: string): Promise<string> {
  const validationError = validateAvatarFile(file);
  if (validationError) throw new ProfileError(validationError);

  const supabase = createClient();
  const path = `${userId}/avatar`;
  const { error: uploadError } = await supabase.storage
    .from("avatars")
    .upload(path, file, { upsert: true, contentType: file.type });
  if (uploadError) throw new ProfileError(uploadError.message);

  const { data } = supabase.storage.from("avatars").getPublicUrl(path);
  // Cache-bust: the path is fixed/reused, so without a query param a
  // browser (or CDN) that already cached the old image at this exact
  // URL would keep showing it after a re-upload — confirmed real
  // behavior, not a hypothetical, since object storage URLs are
  // otherwise maximally cacheable by design.
  const publicUrl = `${data.publicUrl}?v=${Date.now()}`;

  const { error: updateError } = await supabase.auth.updateUser({ data: { avatar_url: publicUrl } });
  if (updateError) throw new ProfileError(updateError.message);

  return publicUrl;
}

/** Investigated (Settings batch 1, item 3): updateUser({ email }) with
 * PKCE flow (this project's default via @supabase/ssr's
 * createBrowserClient, confirmed against the installed SDK) generates a
 * confirmation link shaped identically to password-reset's — a `code`
 * param, exchanged by the SAME already-proven apps/web/app/auth/callback/
 * route.ts via exchangeCodeForSession(). No new callback handling
 * needed.
 *
 * This project's Supabase config has `double_confirm_changes = true`
 * (apps/api/supabase/config.toml), which sends a confirmation link to
 * BOTH the old and new address — but confirming via EITHER ONE alone
 * completes the change immediately. Verified directly against the real
 * local GoTrue instance (two independent full runs: old-link-only and
 * new-link-only both fully swap the email on their own), not assumed
 * from the config option's name/comment, which reads as "both required"
 * but isn't what this GoTrue version actually enforces.
 *
 * REAUTHENTICATION GATE (2026-08-05 security follow-up — see
 * .agent/reviews/2026-08-05-settings-audit.md and .agent/MEMORY.md's
 * "email-change confirmation" entry for the full investigation): the
 * email swap itself completes on a bare, unauthenticated GET to either
 * confirmation link, independent of PKCE — PKCE only protects SESSION/
 * TOKEN issuance at the separate code-exchange step, not the swap.
 * There is no Supabase/GoTrue config flag or Auth Hook (confirmed by
 * checking both) to bind the CONFIRMING request to an active session —
 * this is deliberate cross-device design shared with signup/recovery/
 * magic-link, not something fixable from this client alone. Requiring
 * the CURRENT password before even starting a change doesn't close that
 * gap (it can't, from here) — it raises the bar on who can START one:
 * proves the caller knows the account's real credential right now,
 * rather than just riding an already-open session (shared/public
 * device, a leftover tab), the same reasoning this project's
 * `secure_password_change` config already applies to password changes.
 * Deliberately a real `signInWithPassword` check, not a client-side-only
 * UI gate — a fake "looks verified" success would defeat the point. */
export async function requestEmailChange(
  currentEmail: string,
  currentPassword: string,
  newEmail: string,
  redirectTo: string
): Promise<void> {
  const trimmed = newEmail.trim();
  if (!trimmed) throw new ProfileError("Email must not be empty.");
  if (!currentPassword) throw new ProfileError("Enter your current password to confirm this change.");

  const supabase = createClient();
  const { error: reauthError } = await supabase.auth.signInWithPassword({
    email: currentEmail,
    password: currentPassword,
  });
  if (reauthError) throw new ProfileError("Current password is incorrect.");

  const { error } = await supabase.auth.updateUser({ email: trimmed }, { emailRedirectTo: redirectTo });
  if (error) throw new ProfileError(error.message);
}

/** Investigated (Settings batch 1, item 2 of Part 2): the installed
 * @supabase/auth-js SDK exposes no session/device LIST on either the
 * client or admin API (confirmed via the actual type definitions, not
 * assumed) — there is no "show me every device you're logged in on"
 * capability to build against. What's real: signOut(scope), where
 * 'others' revokes every OTHER session's refresh token while leaving
 * the CURRENT one intact. This is the honest, buildable equivalent of
 * "manage other sessions" — not a device list. */
export async function signOutOtherSessions(): Promise<void> {
  const supabase = createClient();
  const { error } = await supabase.auth.signOut({ scope: "others" });
  if (error) throw new ProfileError(error.message);
}

/** Settings batch 3, part 2 — permanent account deletion. Reauthentication
 * is the SAME real signInWithPassword() check as requestEmailChange
 * above, reused deliberately (item 3 of the task this was built from):
 * a destructive, irreversible action deserves at least the same bar as
 * a metadata change, not less. This function's own job stops at
 * confirming the password and calling the backend — components/settings/
 * delete-account-dialog.tsx additionally requires typing the account's
 * real email before this is ever even invoked (pure UI friction against
 * misclicks, not a security boundary; the security boundary is the
 * password check here).
 *
 * The actual deletion (Storage across all three user-scoped buckets,
 * then the auth.users row itself, which cascades every user-scoped
 * table — routes/account.py has the full ordering/enumeration writeup)
 * happens server-side via the service-role client, which this browser
 * session never has access to — this function only ever calls the
 * backend's DELETE /account with this user's own bearer token, the
 * same trust boundary every other authenticated route in this API
 * already rests on. */
export async function deleteAccount(currentEmail: string, currentPassword: string): Promise<void> {
  if (!currentPassword) throw new ProfileError("Enter your current password to confirm this.");

  const supabase = createClient();
  const { error: reauthError } = await supabase.auth.signInWithPassword({
    email: currentEmail,
    password: currentPassword,
  });
  if (reauthError) throw new ProfileError("Current password is incorrect.");

  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ProfileError("Your session expired — sign in again and retry.");

  const apiUrl = process.env.NEXT_PUBLIC_API_URL!;
  const res = await fetch(`${apiUrl}/account`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ProfileError(body?.error?.message ?? "Couldn't delete your account. Try again.");
  }
}
