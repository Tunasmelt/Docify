const CITATIONS = [
  {
    n: 1,
    label: "MASTER SERVICES AGREEMENT — MERIDIAN · P.14",
    verdict: "SUPPORTED",
    fg: "text-accent",
    bg: "bg-green-bg",
  },
  {
    n: 2,
    label: "MASTER SERVICES AGREEMENT — MERIDIAN · P.15",
    verdict: "PARTIAL",
    fg: "text-amber",
    bg: "bg-amber-bg",
  },
  {
    // A DOCX/PDF handbook gets a real page number (or no locator at all
    // for DOCX specifically) — never a slide number, which only a real
    // PPTX chunk carries (lib/chat/parse-message.ts's citationLocation()).
    n: 3,
    label: "EMPLOYEE HANDBOOK 2026 · P.12",
    verdict: "UNVERIFIED",
    fg: "text-muted",
    bg: "bg-muted-bg",
  },
] as const;

export function LandingProductPreview() {
  return (
    <section id="how" className="border-t border-line bg-panel">
      <div className="mx-auto max-w-[1080px] px-5 py-14 sm:px-8 sm:py-20">
        <div className="mb-11 text-center">
          <p className="mb-3 font-mono text-[11px] tracking-[0.1em] text-faint">
            SEE IT IN ACTION
          </p>
          <h2 className="mx-auto max-w-[640px] text-balance font-serif text-[28px] font-medium leading-[1.2] sm:text-[40px]">
            Answers arrive footnoted. The footnotes are checked.
          </h2>
        </div>

        <div className="mx-auto max-w-[820px] overflow-hidden rounded-xl border border-border bg-bg shadow-[0_20px_60px_rgba(25,23,20,0.10)]">
          <div className="flex h-10 items-center gap-2.5 border-b border-line bg-panel px-4">
            <span className="h-[9px] w-[9px] rounded-full bg-border" />
            <span className="h-[9px] w-[9px] rounded-full bg-border" />
            <span className="h-[9px] w-[9px] rounded-full bg-border" />
            <span className="ml-2 truncate font-mono text-[10px] tracking-[0.08em] text-faint">
              MERIDIAN MSA — TERMINATION TERMS &middot; 2 DOCS IN SCOPE
            </span>
          </div>

          <div className="flex flex-col gap-6 px-5 pb-6 pt-7 sm:px-8">
            <div className="flex justify-end">
              <div className="max-w-[88%] rounded-tl-xl rounded-tr-xl rounded-bl-xl rounded-br-[4px] bg-panel-active px-4 py-3 text-[15px] leading-relaxed sm:max-w-[70%]">
                What notice do we owe Meridian if we terminate early, and does the handbook
                policy conflict?
              </div>
            </div>

            <div className="max-w-[88%]">
              <p className="m-0 text-[15px] leading-[1.8]">
                Terminating for convenience requires 90 days&rsquo; written notice to the
                registered agent
                <sup>
                  <span className="rounded-[3px] bg-green-bg px-[3px] font-mono text-[0.74em] font-medium text-accent">
                    1
                  </span>
                </sup>
                , and fees for work already performed survive termination. Material breach
                shortens this to 30 days with an opportunity to cure
                <sup>
                  <span className="rounded-[3px] border-b border-dotted border-amber bg-amber-bg px-[3px] font-mono text-[0.74em] font-medium text-amber">
                    2
                  </span>
                </sup>
                . The handbook&rsquo;s contractor offboarding window appears to assume 60
                days
                <sup>
                  <span className="rounded-[3px] bg-muted-bg px-[3px] font-mono text-[0.74em] font-medium text-muted line-through decoration-1">
                    3
                  </span>
                </sup>
                , which would need reconc
                <span className="ml-px inline-block h-[1em] w-[1.5px] translate-y-[2px] animate-caret bg-accent align-middle" />
              </p>

              <div className="mt-3.5 flex flex-col gap-2">
                <div className="h-[11px] w-[64%] animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
                <div className="h-[11px] w-[38%] animate-shimmer rounded bg-sk-grad-a bg-[length:400px_100%]" />
              </div>

              <div className="mt-[18px] flex flex-col gap-[7px] border-t border-line pt-3">
                {CITATIONS.map((c) => (
                  <div
                    key={c.n}
                    className="flex items-center gap-2 font-mono text-[11px] tracking-[0.02em] text-faint"
                  >
                    <span className={c.fg}>{c.n}</span>
                    <span className="truncate">{c.label}</span>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 font-medium ${c.fg} ${c.bg}`}>
                      {c.verdict}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="border-t border-line px-5 py-3.5 sm:px-8">
            <div className="flex items-center gap-2.5 rounded-xl border border-border bg-surface px-3.5 py-3">
              <span className="flex-1 text-[15px] text-faint">Ask your documents&hellip;</span>
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-on-accent">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <line x1="12" y1="19" x2="12" y2="5" />
                  <polyline points="5 12 12 5 19 12" />
                </svg>
              </span>
            </div>
          </div>
        </div>

        <div className="mx-auto mt-6 grid max-w-[820px] grid-cols-1 gap-5 sm:grid-cols-3 sm:gap-5">
          <div className="border-l-2 border-accent pl-3.5">
            <p className="m-0 font-mono text-[11px] tracking-[0.08em] text-accent">SUPPORTED</p>
            <p className="mt-1 text-[13.5px] leading-relaxed text-muted">
              The cited passage states the claim. Cite it onward as-is.
            </p>
          </div>
          <div className="border-l-2 border-amber pl-3.5">
            <p className="m-0 font-mono text-[11px] tracking-[0.08em] text-amber">PARTIAL</p>
            <p className="mt-1 text-[13.5px] leading-relaxed text-muted">
              The source backs part of the claim. Read the page before you rely on it.
            </p>
          </div>
          <div className="border-l-2 border-border pl-3.5">
            <p className="m-0 font-mono text-[11px] tracking-[0.08em] text-muted">UNVERIFIED</p>
            <p className="mt-1 text-[13.5px] leading-relaxed text-muted">
              The check couldn&rsquo;t confirm it. Shown struck through rather than quietly
              dropped.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
