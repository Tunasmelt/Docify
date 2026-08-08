import { LandingHeader } from "@/components/landing/landing-header";
import { LandingHero } from "@/components/landing/landing-hero";
import { LandingProductPreview } from "@/components/landing/landing-product-preview";
import { LandingFeatures } from "@/components/landing/landing-features";
import { LandingFinalCta } from "@/components/landing/landing-final-cta";
import { LandingFooter } from "@/components/landing/landing-footer";

export function LandingPage() {
  return (
    <div className="bg-bg text-ink">
      <LandingHeader />
      <LandingHero />
      <LandingProductPreview />
      <LandingFeatures />
      <LandingFinalCta />
      <LandingFooter />
    </div>
  );
}
