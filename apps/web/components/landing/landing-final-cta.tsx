import Link from "next/link";

import { Button } from "@/components/ui/button";

export function LandingFinalCta() {
  return (
    <section className="border-t border-line bg-panel">
      <div className="mx-auto max-w-[1080px] px-5 py-16 text-center sm:px-8 sm:py-20">
        <h2 className="m-0 mb-3.5 font-serif text-[28px] font-medium leading-[1.2] sm:text-[40px]">
          Start with one document.
        </h2>
        <p className="mx-auto mb-[30px] max-w-[520px] text-[17px] leading-relaxed text-muted">
          Upload something you already need answers from, and see what the footnotes say.
        </p>
        <Button asChild size="lg">
          <Link href="/signup">Create an account</Link>
        </Button>
      </div>
    </section>
  );
}
