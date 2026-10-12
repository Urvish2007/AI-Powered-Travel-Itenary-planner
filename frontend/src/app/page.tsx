import { DestinationRail } from "@/components/home/destination-rail";
import { Hero } from "@/components/home/hero";
import {
  ClosingCta,
  HowItWorks,
  Testimonials,
  WhyDifferent,
} from "@/components/home/sections";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export default function HomePage() {
  return (
    <>
      {/* Transparent over the hero photograph; it solidifies on scroll. */}
      <SiteHeader transparent />
      <main id="main">
        <Hero />
        <DestinationRail />
        <HowItWorks />
        <WhyDifferent />
        <Testimonials />
        <ClosingCta />
      </main>
      <SiteFooter />
    </>
  );
}
