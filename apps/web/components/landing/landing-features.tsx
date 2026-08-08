const FEATURES = [
  {
    n: "01",
    label: "INGEST",
    title: "Four formats, one library",
    superscript: false,
    body: "PDF, DOCX, PPTX, and HTML. Text is extracted, chunked, and indexed on upload, with page and slide boundaries preserved so a citation can point at exactly where it came from.",
  },
  {
    n: "02",
    label: "ASK",
    title: "Answers that stream",
    superscript: false,
    body: "Ask in plain language across one document or several. Text arrives token by token while retrieval and verification finish behind it, so a long answer reads as it's written.",
  },
  {
    n: "03",
    label: "VERIFY",
    title: "Citations checked after the fact",
    superscript: true,
    body: "Generation and verification are separate steps. Once an answer exists, each citation is re-checked against the chunk it names and labelled supported, partial, or unverified. A model asserting its own sources is not evidence; this is.",
  },
  {
    n: "04",
    label: "TRACE",
    title: "One click to the page itself",
    superscript: false,
    body: "Click any footnote to open the source: the document, the page or slide, and the exact excerpt the claim was drawn from — including the figure or table when the chunk is one.",
  },
] as const;

export function LandingFeatures() {
  return (
    <section className="mx-auto max-w-[1080px] px-5 py-16 sm:px-8 sm:py-[88px]">
      <div className="grid grid-cols-1 gap-11 sm:grid-cols-2 sm:gap-x-16 sm:gap-y-14">
        {FEATURES.map((f) => (
          <div key={f.n}>
            <p className="m-0 mb-2.5 font-mono text-[11px] tracking-[0.1em] text-accent">
              {f.n} &middot; {f.label}
            </p>
            <h3 className="m-0 mb-2.5 font-serif text-2xl font-medium leading-[1.3] sm:text-[26px]">
              {f.title}
              {f.superscript ? <sup className="text-sm text-accent">1</sup> : null}
            </h3>
            <p className="m-0 text-[15px] leading-relaxed text-muted">{f.body}</p>
          </div>
        ))}
      </div>

      <div className="mt-14 flex flex-col items-start gap-3.5 rounded-xl border border-line bg-panel p-6 sm:mt-16 sm:flex-row sm:gap-5 sm:p-7">
        <span className="whitespace-nowrap pt-0.5 font-mono text-[11px] tracking-[0.1em] text-faint">
          ARCHITECTURE
        </span>
        <p className="m-0 text-[15px] leading-relaxed text-muted">
          Multi-tenant by design: every row carries a tenant, and isolation is enforced at the
          database with row-level security rather than in application code. Queries can only
          reach the tenant on the session &mdash; a bug in the API layer doesn&rsquo;t become a
          data leak.
        </p>
      </div>
    </section>
  );
}
