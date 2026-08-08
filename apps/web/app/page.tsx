"use client";

import { LandingPage } from "@/components/landing/landing-page";

// An authenticated visitor never reaches this component — middleware.ts
// redirects "/" to /documents for them first, the same way it already
// does for /login and /signup. This route is reachable only when
// unauthenticated, so it renders the landing page unconditionally.
export default function RootPage() {
  return <LandingPage />;
}
