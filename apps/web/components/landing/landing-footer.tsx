import Link from "next/link";

import { GithubIcon } from "@/components/landing/github-icon";
import { REPO_URL, REPO_LABEL } from "@/components/landing/repo";

export function LandingFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto flex max-w-[1080px] flex-col items-start justify-between gap-8 px-5 py-11 sm:flex-row sm:px-8 sm:py-14">
        <div>
          <span className="font-serif text-xl font-semibold">
            Docify<sup className="text-[11px] font-medium text-accent">1</sup>
          </span>
          <p className="m-0 mt-2 max-w-[340px] font-mono text-[11px] leading-relaxed tracking-[0.04em] text-faint">
            <span className="text-accent">1</span>
            &nbsp; Every answer cites the exact source page. Verified, not guessed.
          </p>
        </div>
        <div className="flex gap-14">
          <div className="flex flex-col gap-2.5">
            <span className="font-mono text-[10px] tracking-[0.1em] text-faint">PRODUCT</span>
            <Link href="/login" className="text-sm text-muted hover:text-ink hover:no-underline">
              Sign in
            </Link>
            <Link href="/signup" className="text-sm text-muted hover:text-ink hover:no-underline">
              Create account
            </Link>
          </div>
          <div className="flex flex-col gap-2.5">
            <span className="font-mono text-[10px] tracking-[0.1em] text-faint">SOURCE</span>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-sm text-muted hover:text-ink hover:no-underline"
            >
              <GithubIcon size={14} />
              {REPO_LABEL}
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
