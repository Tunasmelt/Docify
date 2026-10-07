import type { Metadata } from "next";
import localFont from "next/font/local";

import { ThemeProvider } from "@/components/theme-provider";

import "./globals.css";

// Bundle the same families locally: builds must not depend on Google Fonts
// URL formats or availability. Licence notices live beside these assets.
const newsreader = localFont({
  src: [
    { path: "./fonts/newsreader-normal.woff2", weight: "400 600", style: "normal" },
    { path: "./fonts/newsreader-italic.woff2", weight: "400 600", style: "italic" },
  ],
  variable: "--font-newsreader",
  display: "swap",
  adjustFontFallback: "Times New Roman",
});

const splineSans = localFont({
  src: "./fonts/spline-sans-normal.woff2",
  weight: "400 600",
  variable: "--font-spline-sans",
  display: "swap",
});

const splineSansMono = localFont({
  src: "./fonts/spline-sans-mono-normal.woff2",
  weight: "400 500",
  variable: "--font-spline-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Docify",
  description: "Ask your documents. Get answers with receipts.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${newsreader.variable} ${splineSans.variable} ${splineSansMono.variable}`}
      suppressHydrationWarning
    >
      <body className="antialiased">
        <ThemeProvider attribute="data-theme" defaultTheme="light" enableSystem={false}>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
