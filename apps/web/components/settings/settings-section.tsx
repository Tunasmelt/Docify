"use client";

import * as React from "react";

export interface SettingsSectionProps {
  title: string;
  description?: string;
  children: React.ReactNode;
}

/** Thin structural wrapper, not a new design-system primitive — same
 * `rounded-lg border border-line bg-drop-bg` card treatment already
 * used for the documents/conversations list containers, just reused
 * here since Settings is naturally several stacked sections. */
export function SettingsSection({ title, description, children }: SettingsSectionProps) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-drop-bg">
      <div className="border-b border-line px-6 py-4">
        <h2 className="m-0 font-serif text-[19px] font-medium">{title}</h2>
        {description ? <p className="m-0 mt-1 text-[13px] leading-relaxed text-muted">{description}</p> : null}
      </div>
      <div className="flex flex-col gap-5 px-6 py-5">{children}</div>
    </section>
  );
}
