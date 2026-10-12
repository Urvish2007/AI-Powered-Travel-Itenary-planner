import type { Metadata } from "next";

import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { SharedItinerary } from "@/components/trip/shared-itinerary";

export const metadata: Metadata = {
  title: "A shared itinerary",
  description: "An itinerary someone shared with you, planned with Waypoint.",
  // A share link is unguessable but not secret; keeping it out of search
  // results is the polite default.
  robots: { index: false, follow: false },
};

export default async function SharedPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  return (
    <>
      <SiteHeader />
      <main id="main" className="pt-16 sm:pt-18">
        <SharedItinerary token={token} />
      </main>
      <SiteFooter />
    </>
  );
}
