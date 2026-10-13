import type { Metadata } from "next";

import { RequireAuth } from "@/components/auth/require-auth";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { TripsDashboard } from "@/components/trip/trips-dashboard";

export const metadata: Metadata = {
  title: "My trips",
  description: "Every itinerary you have planned with Waypoint.",
};

export default function TripsPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="min-h-[70svh] pt-16 sm:pt-18">
        <RequireAuth>
          <TripsDashboard />
        </RequireAuth>
      </main>
      <SiteFooter />
    </>
  );
}
