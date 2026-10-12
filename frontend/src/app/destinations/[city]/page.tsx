import type { Metadata } from "next";

import { CityPage } from "@/components/places/city-page";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { titleCase } from "@/lib/utils";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ city: string }>;
}): Promise<Metadata> {
  const { city } = await params;
  const name = titleCase(decodeURIComponent(city));
  return {
    title: `Things to do in ${name}`,
    description: `Browse places in ${name} and build a day-by-day itinerary that fits your pace.`,
  };
}

export default async function DestinationPage({
  params,
}: {
  params: Promise<{ city: string }>;
}) {
  const { city } = await params;

  return (
    <>
      <SiteHeader transparent />
      <main id="main">
        <CityPage citySlug={decodeURIComponent(city)} />
      </main>
      <SiteFooter />
    </>
  );
}
