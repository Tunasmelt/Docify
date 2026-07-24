export type DocumentStatus = "uploaded" | "parsing" | "embedded" | "ready" | "failed";

export interface StatusStyle {
  label: string;
  fg: string;
  bg: string;
  /** Pulsing status dot — reserved for the one status that's actively
   * in progress server-side. */
  live: boolean;
}

/** CSS custom properties, not hardcoded hex — the design tokens already
 * switch value under `[data-theme="dark"]`, so referencing var(--x)
 * here keeps status colors theme-correct without a parallel light/dark
 * branch (the prototype computed light/dark hex pairs by hand; the real
 * token system makes that redundant). */
export const DOCUMENT_STATUS_STYLES: Record<DocumentStatus, StatusStyle> = {
  uploaded: { label: "Uploaded", fg: "var(--muted)", bg: "var(--muted-bg)", live: false },
  parsing: { label: "Parsing", fg: "var(--amber)", bg: "var(--amber-bg)", live: true },
  embedded: { label: "Embedding", fg: "var(--amber)", bg: "var(--amber-bg)", live: true },
  ready: { label: "Ready", fg: "var(--accent)", bg: "var(--green-bg)", live: false },
  failed: { label: "Failed", fg: "var(--destructive)", bg: "var(--destructive-bg)", live: false },
};

export const CITATION_VERDICT_STYLES = {
  supported: { fg: "var(--accent)", bg: "var(--green-bg)" },
  partial: { fg: "var(--amber)", bg: "var(--amber-bg)" },
} as const;
