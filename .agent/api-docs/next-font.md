# Next.js local fonts

Verified 2026-10-07 against the installed Next.js 14.2.35 loader and official Next.js 14 documentation: https://nextjs.org/docs/14/app/building-your-application/optimizing/fonts#local-fonts

`next/font/local` accepts a relative `src` path, or an array with `path`, `weight` and `style`. Variable fonts use a weight range string. `variable` exposes the existing CSS custom property; `display: swap` preserves loading behavior. Newsreader uses normal and italic local files and Times New Roman fallback; the sans and mono fonts keep their existing variables. Assets are bundled at build time without Google Fonts requests. Original SIL OFL notices and download provenance live in apps/web/app/fonts.

The installed Google loader assumes returned font URLs end in a font extension. CI exposed a TypeError at its `.exec(url)[1]` when a URL did not meet that assumption. Local files remove this network/URL-format dependency without a Next.js upgrade.
