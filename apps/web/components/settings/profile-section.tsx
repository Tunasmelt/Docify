"use client";

import * as React from "react";
import { Check } from "lucide-react";

import { SettingsSection } from "@/components/settings/settings-section";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { CurrentUser } from "@/hooks/use-current-user";
import { ProfileError, updateDisplayName, uploadAvatar, validateAvatarFile } from "@/lib/supabase/profile";

const SAVED_CONFIRM_MS = 1500;

export interface ProfileSectionProps {
  user: CurrentUser;
}

/** Part 1, items 1 + 2 — display name (user_metadata, no schema change)
 * and avatar upload (public `avatars` bucket, migrations/
 * 20260804_001_avatars_bucket.sql). One section, since both are
 * "how you're identified" — matches this batch's own Part 1 framing. */
export function ProfileSection({ user }: ProfileSectionProps) {
  const [name, setName] = React.useState(user.name);
  const [savingName, setSavingName] = React.useState(false);
  const [nameSaved, setNameSaved] = React.useState(false);
  const [nameError, setNameError] = React.useState<string | null>(null);
  const savedResetRef = React.useRef<ReturnType<typeof setTimeout>>();

  const [avatarPreview, setAvatarPreview] = React.useState<string | null>(null);
  const [uploadingAvatar, setUploadingAvatar] = React.useState(false);
  const [avatarError, setAvatarError] = React.useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => () => clearTimeout(savedResetRef.current), []);

  async function handleSaveName(e: React.FormEvent) {
    e.preventDefault();
    setNameError(null);
    try {
      setSavingName(true);
      await updateDisplayName(name);
      setNameSaved(true);
      clearTimeout(savedResetRef.current);
      savedResetRef.current = setTimeout(() => setNameSaved(false), SAVED_CONFIRM_MS);
    } catch (err) {
      setNameError(err instanceof ProfileError ? err.message : "Couldn't save your name. Try again.");
    } finally {
      setSavingName(false);
    }
  }

  function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setAvatarError(null);

    const validationError = validateAvatarFile(file);
    if (validationError) {
      setAvatarError(validationError);
      e.target.value = "";
      return;
    }

    // Real client-side preview (task's own spec) — object URL, revoked
    // once the real upload settles either way, not left dangling.
    const objectUrl = URL.createObjectURL(file);
    setAvatarPreview(objectUrl);

    setUploadingAvatar(true);
    uploadAvatar(file, user.id)
      .catch((err) => {
        setAvatarError(err instanceof ProfileError ? err.message : "Couldn't upload your avatar. Try again.");
        setAvatarPreview(null);
      })
      .finally(() => {
        setUploadingAvatar(false);
        URL.revokeObjectURL(objectUrl);
        e.target.value = "";
      });
  }

  const displayedAvatar = avatarPreview ?? user.avatarUrl;

  return (
    <SettingsSection title="Profile" description="How you're identified across Docify.">
      <div className="flex items-center gap-4">
        {displayedAvatar ? (
          // eslint-disable-next-line @next/next/no-img-element -- public
          // Storage URL / a transient local object URL preview, neither
          // of which benefits from next/image's remote-pattern caching.
          <img src={displayedAvatar} alt="" className="h-16 w-16 flex-shrink-0 rounded-full object-cover" />
        ) : (
          <div className="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-full bg-accent text-xl font-semibold text-on-accent">
            {user.initials}
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            className="hidden"
            data-testid="avatar-file-input"
            onChange={handleFileSelected}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={uploadingAvatar}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploadingAvatar ? "Uploading…" : "Change avatar"}
          </Button>
          <p className="m-0 text-[11px] text-faint">JPEG, PNG, WebP, or GIF. Up to 5 MB.</p>
          {avatarError ? <p className="m-0 text-[13px] text-destructive">{avatarError}</p> : null}
        </div>
      </div>

      <form onSubmit={handleSaveName} className="flex flex-col gap-1.5">
        <label htmlFor="display-name" className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
          Display name
        </label>
        <div className="flex items-center gap-2.5">
          <Input
            id="display-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="max-w-[320px]"
          />
          <Button type="submit" size="sm" disabled={!name.trim() || savingName || name.trim() === user.name}>
            {nameSaved ? (
              <>
                <Check size={14} strokeWidth={2} /> Saved
              </>
            ) : savingName ? (
              "Saving…"
            ) : (
              "Save"
            )}
          </Button>
        </div>
        {nameError ? <p className="m-0 text-[13px] text-destructive">{nameError}</p> : null}
      </form>
    </SettingsSection>
  );
}
