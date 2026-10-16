"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MapPin, Sparkles, Star } from "lucide-react";
import Link from "next/link";

import { CityBackdrop, PhotoCredit } from "@/components/places/city-backdrop";
import { CityExplorer } from "@/components/places/city-explorer";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { pluralize, titleCase } from "@/lib/utils";

export function CityPage({ citySlug }: { citySlug: string }) {
  const { data: cities, isLoading } = useQuery({
    queryKey: ["cities"],
    queryFn: () => api.places.cities(),
    staleTime: 10 * 60_000,
  });

  // The URL carries a lower-cased slug; the canonical name comes from the API
  // so the heading reads "San Francisco", not "San francisco".
  const city = cities?.find(
    (candidate) => candidate.city.toLowerCase() === citySlug.toLowerCase(),
  );

  if (isLoading) {
    return (
      <>
        <Skeleton className="h-[42svh] w-full rounded-none" />
        <div className="mx-auto max-w-7xl space-y-6 px-4 py-12 sm:px-6 lg:px-8">
          <Skeleton className="h-11 w-full rounded-xl" />
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-80 rounded-2xl" />
            ))}
          </div>
        </div>
      </>
    );
  }

  if (!city) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-32">
        <EmptyState
          icon={<MapPin />}
          title={`We don't cover ${titleCase(citySlug)} yet`}
          description="A destination appears once we have enough place data to schedule a real day around it."
          action={
            <Button asChild size="lg">
              <Link href="/destinations">
                <ArrowLeft />
                See where we do plan
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <>
      <section className="relative isolate flex min-h-[46svh] items-end">
        <div className="absolute inset-0 -z-10">
          <CityBackdrop
            city={city.city}
            country={city.country}
            cityPhoto={city.photo}
            priority
            sizes="100vw"
            className="object-cover"
            showCredit={false}
          />
        </div>
        <div className="absolute inset-0 -z-10 bg-gradient-to-t from-slate-950/92 via-slate-950/45 to-slate-950/55" />

        {/* Above the gradient, so it stays legible and clickable. */}
        {city.photo?.image_url && (
          <PhotoCredit photo={city.photo} linked className="bottom-3 right-3 top-auto" />
        )}

        <div className="mx-auto w-full max-w-7xl px-4 pb-10 pt-28 sm:px-6 lg:px-8 lg:pb-14">
          <Link
            href="/destinations"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-white/80 transition-colors hover:text-white"
          >
            <ArrowLeft className="size-4" />
            All destinations
          </Link>

          <h1 className="mt-4 text-4xl font-extrabold text-white sm:text-5xl lg:text-6xl">
            {city.city}
          </h1>
          <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-white/85">
            <span className="flex items-center gap-1.5">
              <MapPin className="size-4" />
              {city.country}
            </span>
            <span>
              {city.place_count} {pluralize(city.place_count, "place")} mapped
            </span>
            {city.average_rating > 0 && (
              <span className="flex items-center gap-1.5">
                <Star className="size-4 fill-star text-star" />
                {city.average_rating.toFixed(1)} average
              </span>
            )}
          </p>

          <Button asChild size="lg" className="mt-7">
            <Link href={`/plan?destination=${encodeURIComponent(city.city)}`}>
              <Sparkles />
              Plan a trip here
            </Link>
          </Button>
        </div>
      </section>

      <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
        <CityExplorer city={city.city} />
      </div>
    </>
  );
}
