"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar, MobileMenuButton } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { ProfileSection } from "@/components/settings/profile-section";
import { EmailSection } from "@/components/settings/email-section";
import { SecuritySection } from "@/components/settings/security-section";
import { AppearanceSection } from "@/components/settings/appearance-section";
import { PreferencesSection } from "@/components/settings/preferences-section";
import { ExportSection } from "@/components/settings/export-section";
import { DangerZoneSection } from "@/components/settings/danger-zone-section";
import { createClient } from "@/lib/supabase/browser";
import { useCurrentUser } from "@/hooks/use-current-user";

const EMPTY_USER = { initials: "", name: "", email: "" };

export default function SettingsPage() {
  const router = useRouter();
  const supabase = React.useMemo(() => createClient(), []);
  const currentUser = useCurrentUser();
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="grid min-h-screen grid-cols-1 bg-bg text-ink md:grid-cols-[248px_1fr]">
      <Sidebar
        user={currentUser ?? EMPTY_USER}
        mobileOpen={mobileMenuOpen}
        onMobileClose={() => setMobileMenuOpen(false)}
        onSignOut={handleSignOut}
      />
      <div className="flex min-w-0 flex-col">
        <Topbar
          left={
            <>
              <MobileMenuButton onClick={() => setMobileMenuOpen(true)} />
              <span className="truncate text-sm font-semibold">Settings</span>
            </>
          }
          right={<ThemeToggle />}
        />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-[640px] flex-col gap-6 px-6 py-10">
            <h1 className="m-0 font-serif text-[28px] font-medium">
              Settings
              <sup className="text-sm font-normal text-accent">1</sup>
            </h1>
            {currentUser ? (
              <>
                <ProfileSection user={currentUser} />
                <EmailSection currentEmail={currentUser.email} />
                <SecuritySection />
                <AppearanceSection />
                <PreferencesSection />
                <ExportSection />
                <DangerZoneSection accountEmail={currentUser.email} />
              </>
            ) : (
              <div className="flex flex-col gap-6">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-32 animate-shimmer rounded-lg bg-sk-grad-a bg-[length:400px_100%]" />
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
