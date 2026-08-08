import Link from "next/link";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { GithubIcon } from "@/components/landing/github-icon";
import { REPO_URL } from "@/components/landing/repo";

export function LandingHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/86 backdrop-blur-[10px]">
      <div className="mx-auto flex h-16 max-w-[1080px] items-center justify-between px-5 sm:px-8">
        <span className="font-serif text-[22px] font-semibold">
          Docify<sup className="text-xs font-medium text-accent">1</sup>
        </span>
        <div className="flex items-center gap-3 sm:gap-6">
          <a
            href="#how"
            className="hidden text-sm font-medium text-muted hover:text-ink hover:no-underline sm:inline"
          >
            How it works
          </a>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hidden items-center gap-1.5 text-sm font-medium text-muted hover:text-ink hover:no-underline sm:inline-flex"
          >
            <GithubIcon size={15} />
            Source
          </a>
          <Link
            href="/login"
            className="hidden text-sm font-medium text-muted hover:text-ink hover:no-underline sm:inline"
          >
            Sign in
          </Link>
          <ThemeToggle />
          <Button asChild size="sm">
            <Link href="/signup">Create account</Link>
          </Button>
        </div>
      </div>
    </header>
  );
}
