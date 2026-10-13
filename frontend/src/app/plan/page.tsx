import type { Metadata } from "next";
import { Suspense } from "react";

import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { PlanWizard } from "@/components/trip/plan-wizard";
import { Skeleton } from "@/components/ui/primitives";

export const metadata: Metadata = {
  title: "Plan a trip",
  description:
    "Tell us where you're going and how you like to travel. We'll build a day-by-day itinerary that fits.",
};

export default function PlanPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="pt-16 sm:pt-18">
        <Suspense
          fallback={
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-14">
              <Skeleton className="h-2 w-full" />
              <Skeleton className="h-10 w-2/3" />
              <Skeleton className="h-64 w-full rounded-2xl" />
            </div>
          }
        >
          <PlanWizard />
        </Suspense>
      </main>
      <SiteFooter />
    </>
  );
}
