import Link from "next/link";

import { Button } from "@/components/ui/button";
import { GithubIcon } from "@/components/landing/github-icon";
import { REPO_URL } from "@/components/landing/repo";

export function LandingHero() {
  return (
    <section className="mx-auto max-w-[1080px] animate-fade-up px-5 pb-16 pt-20 text-center sm:px-8 sm:pb-[88px] sm:pt-[104px]">
      <span className="inline-block rounded-full border border-border px-3.5 py-1.5 font-mono text-[11px] tracking-[0.1em] text-muted">
        PDF &middot; DOCX &middot; PPTX &middot; HTML
      </span>
      <h1 className="mx-auto mb-6 mt-7 max-w-[820px] text-balance font-serif text-[38px] font-medium leading-[1.1] tracking-[-0.015em] sm:text-[68px] sm:leading-[1.08]">
        Ask your documents.
        <br />
        Get answers <em className="italic text-accent">with receipts</em>.
        <sup className="text-2xl text-accent sm:text-[30px]">1</sup>
      </h1>
      <p className="mx-auto max-w-[660px] text-balance text-[16.5px] leading-relaxed text-muted sm:text-[19px] sm:leading-[1.65]">
        Upload PDFs, Word docs, slide decks, or HTML. Ask in plain language and watch answers
        stream in. Every citation is independently verified against the source after generation
        &mdash; then labelled supported, partial, or unverified, so you know which claims to
        trust before you act on them.
      </p>
      <div className="mt-9 flex flex-col items-stretch justify-center gap-3.5 sm:flex-row sm:items-center">
        <Button asChild size="lg">
          <Link href="/signup">Create an account</Link>
        </Button>
        <Button asChild variant="outline" size="lg" className="justify-center gap-2.5">
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
            <GithubIcon size={17} />
            Read the source
          </a>
        </Button>
      </div>
      <p className="mt-8 font-mono text-xs tracking-[0.04em] text-faint">
        <span className="text-accent">1</span>
        &nbsp; A receipt is a page number, a slide, an excerpt &mdash; and a verdict on whether
        it actually holds.
      </p>
    </section>
  );
}
