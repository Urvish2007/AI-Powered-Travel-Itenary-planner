"use client";

import { useQuery } from "@tanstack/react-query";
import { LinkIcon } from "lucide-react";
import Link from "next/link";

import { ItineraryView } from "@/components/trip/itinerary-view";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";

/**
 * A publicly shared itinerary.
 *
 * Read-only by construction: no trip id is passed through, so the edit and
 * delete paths in ItineraryView are never reachable from here.
 */
export function SharedItinerary({ token }: { token: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["shared", token],
    queryFn: () => api.trips.shared(token),
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-14">
        <Skeleton className="h-10 w-2/3 max-w-lg" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-24">
        <EmptyState
          icon={<LinkIcon />}
          title="This link is no longer active"
          description="The person who shared it may have turned sharing off, or the link was mistyped."
          action={
            <Button asChild size="lg">
              <Link href="/plan">Plan your own trip</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <>
      <ItineraryView sharedItinerary={data} readOnly />
      <section className="border-t border-border bg-secondary/40 py-14">
        <div className="mx-auto max-w-2xl px-4 text-center">
          <h2 className="text-2xl font-bold">Want one like this?</h2>
          <p className="mt-2 text-muted-foreground">
            Build your own itinerary in a couple of minutes. It is free.
          </p>
          <Button asChild size="lg" className="mt-6">
            <Link href="/plan">Plan my trip</Link>
          </Button>
        </div>
      </section>
    </>
  );
}
