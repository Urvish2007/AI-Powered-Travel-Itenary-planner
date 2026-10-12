import type { Metadata } from "next";

import { DestinationsGrid } from "@/components/places/destinations-grid";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export const metadata: Metadata = {
  title: "Destinations",
  description:
    "Cities we hold detailed data for — real opening hours, visit lengths and coordinates for every place.",
};

export default function DestinationsPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="min-h-[70svh] pt-16 sm:pt-18">
        <DestinationsGrid />
      </main>
      <SiteFooter />
    </>
  );
}
