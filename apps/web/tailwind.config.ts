import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        ink: "var(--ink)",
        muted: "var(--muted)",
        faint: "var(--faint)",
        line: "var(--line)",
        border: "var(--border)",
        panel: "var(--panel)",
        "panel-active": "var(--panel-active)",
        "panel-hover": "var(--panel-hover)",
        accent: "var(--accent)",
        "accent-hover": "var(--accent-hover)",
        "on-accent": "var(--on-accent)",
        "dashed-line": "var(--dashed)",
        "drop-bg": "var(--drop-bg)",
        "drop-bg-hover": "var(--drop-bg-hover)",
        "focus-ring": "var(--focus-ring)",
        destructive: "var(--destructive)",
        "destructive-hover": "var(--destructive-hover)",
        "destructive-bg": "var(--destructive-bg)",
        amber: "var(--amber)",
        "amber-bg": "var(--amber-bg)",
        "green-bg": "var(--green-bg)",
        "muted-bg": "var(--muted-bg)",
      },
      backgroundImage: {
        "sk-grad-a": "var(--sk-grad-a)",
        "sk-grad-b": "var(--sk-grad-b)",
        "sk-grad-c": "var(--sk-grad-c)",
      },
      fontFamily: {
        serif: ["var(--font-newsreader)", "serif"],
        sans: ["var(--font-spline-sans)", "sans-serif"],
        mono: ["var(--font-spline-mono)", "monospace"],
      },
      keyframes: {
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
        pulseDot: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        fadeUp: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        slideIn: {
          from: { transform: "translateX(24px)", opacity: "0" },
          to: { transform: "none", opacity: "1" },
        },
        indeterminate: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(300%)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.4s linear infinite",
        "pulse-dot": "pulseDot 1.2s ease-in-out infinite",
        "fade-up": "fadeUp 0.5s ease both",
        "slide-in": "slideIn 0.25s ease both",
        "indeterminate-bar": "indeterminate 1.2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
