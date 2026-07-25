"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, X } from "lucide-react";

import { cn } from "@/lib/utils";

function DocumentsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function ConversationsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

const NAV_ITEMS = [
  { href: "/documents", label: "Documents", icon: DocumentsIcon },
  { href: "/chat", label: "Conversations", icon: ConversationsIcon },
  { href: "#", label: "Settings", icon: SettingsIcon },
] as const;

export interface SidebarUser {
  initials: string;
  name: string;
  email: string;
}

export interface SidebarProps {
  /** Rendered below the nav — e.g. the document library list or a
   * recent-conversations list. Each page owns its own content here. */
  librarySection?: React.ReactNode;
  user: SidebarUser;
  /** Below the `md` breakpoint the sidebar renders as an off-canvas
   * drawer over a scrim (matching the reference's mobile pattern)
   * instead of a permanent 248px column — `mobileOpen` controls it;
   * at `md` and above it always renders as the normal static column
   * regardless of this prop. */
  mobileOpen: boolean;
  onMobileClose: () => void;
  onSignOut: () => void;
}

export function Sidebar({ librarySection, user, mobileOpen, onMobileClose, onSignOut }: SidebarProps) {
  const pathname = usePathname();

  return (
    <>
      {mobileOpen ? (
        <div
          className="fixed inset-0 z-40 bg-[rgba(25,23,20,0.4)] md:hidden"
          onClick={onMobileClose}
          aria-hidden="true"
        />
      ) : null}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[248px] flex-shrink-0 flex-col border-r border-line bg-panel transition-transform duration-200 ease-out",
          "md:relative md:z-auto md:translate-x-0 md:shadow-none",
          mobileOpen ? "translate-x-0 shadow-[8px_0_32px_rgba(25,23,20,0.18)]" : "-translate-x-full"
        )}
      >
        <div className="flex items-center justify-between px-5 pb-4 pt-5">
          <div className="font-serif text-[21px] font-semibold">
            Docify
            <sup className="ml-0.5 text-xs font-medium text-accent">1</sup>
          </div>
          <button
            type="button"
            onClick={onMobileClose}
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted md:hidden"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </div>
        <nav className="flex flex-col gap-0.5 px-3">
          {NAV_ITEMS.map((item) => {
            const isActive = item.href !== "#" && pathname?.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.label}
                href={item.href}
                onClick={onMobileClose}
                className={cn(
                  "flex min-h-11 items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium no-underline transition-colors",
                  isActive
                    ? "bg-panel-active text-ink"
                    : "text-muted hover:bg-panel-hover hover:text-ink"
                )}
              >
                <Icon />
                {item.label}
              </Link>
            );
          })}
        </nav>
        {librarySection}
        <div className="mt-auto flex items-center gap-2.5 border-t border-line px-4 py-3.5">
          <div className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-accent text-[12px] font-semibold text-on-accent">
            {user.initials}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium">{user.name}</div>
            <div className="truncate text-[11px] text-faint">{user.email}</div>
          </div>
          <button
            type="button"
            title="Sign out"
            onClick={onSignOut}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-faint hover:bg-panel-hover hover:text-ink"
          >
            <LogOut size={16} strokeWidth={2} />
          </button>
        </div>
      </aside>
    </>
  );
}
