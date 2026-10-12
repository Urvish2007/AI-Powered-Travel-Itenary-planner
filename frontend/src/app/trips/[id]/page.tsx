import type { Metadata } from "next";

import { SiteHeader } from "@/components/layout/site-header";
import { SiteFooter } from "@/components/layout/site-footer";
import { ItineraryView } from "@/components/trip/itinerary-view";
import { RequireAuth } from "@/components/auth/require-auth";

export const metadata: Metadata = {
  title: "Your itinerary",
};

// params is a Promise in Next 16 and must be awaited.
export default async function TripPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <SiteHeader />
      <div id="main" className="pt-16 sm:pt-18">
        <RequireAuth>
          <ItineraryView tripId={id} />
        </RequireAuth>
      </div>
      <SiteFooter />
    </>
  );
}
