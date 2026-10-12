"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, MapPin } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Rating, SectionHeading, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { blurPhoto, cityImage, photo } from "@/lib/images";
import { pluralize } from "@/lib/utils";
import type { CitySummary } from "@/types/api";

/**
 * Destinations we hold data for, pulled live from the API.
 *
 * The card count is driven by the backend rather than hard-coded, so seeding a
 * new city makes it appear here with no frontend change.
 */
export function DestinationRail() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["cities"],
    queryFn: () => api.places.cities(),
    staleTime: 10 * 60_000,
  });

  const cities = (data ?? []).slice(0, 8);

  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 lg:px-8 lg:py-24">
      <SectionHeading
        eyebrow="Destinations"
        title="Places we know inside out"
        description="Each city is backed by real opening hours, visit lengths and coordinates — the details a plan lives or dies on."
        action={
          <Button asChild variant="outline">
            <Link href="/destinations">
              See all
              <ArrowRight />
            </Link>
          </Button>
        }
      />

      {isError ? (
        <p className="mt-10 rounded-2xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
          Destinations could not be loaded right now. The planner still works —
          head to{" "}
          <Link href="/plan" className="font-medium text-primary underline">
            plan a trip
          </Link>
          .
        </p>
      ) : (
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {isLoading
            ? Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-72 rounded-2xl" />
              ))
            : cities.map((city, index) => (
                <DestinationCard
                  key={`${city.city}-${city.country}`}
                  city={city}
                  priority={index < 2}
                />
              ))}
        </div>
      )}
    </section>
  );
}

function DestinationCard({
  city,
  priority,
}: {
  city: CitySummary;
  priority?: boolean;
}) {
  const imageKey = cityImage(city.city);

  return (
    <Link
      href={`/destinations/${encodeURIComponent(city.city.toLowerCase())}`}
      className="group relative block h-72 overflow-hidden rounded-2xl shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <Image
        src={photo(imageKey, { width: 700, height: 900 })}
        alt={`${city.city}, ${city.country}`}
        fill
        priority={priority}
        sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 25vw"
        placeholder="blur"
        blurDataURL={blurPhoto(imageKey)}
        className="object-cover transition-transform duration-700 group-hover:scale-105"
      />
      <div className="absolute inset-0 scrim" />

      <div className="absolute inset-x-0 bottom-0 p-5 text-white">
        <p className="flex items-center gap-1.5 text-xs font-medium text-white/80">
          <MapPin className="size-3.5" />
          {city.country}
        </p>
        <h3 className="mt-1 text-xl font-bold">{city.city}</h3>
        <div className="mt-2.5 flex items-center justify-between">
          <span className="text-sm text-white/80">
            {city.place_count} {pluralize(city.place_count, "place")}
          </span>
          {city.average_rating > 0 && (
            <Rating value={city.average_rating} showValue className="text-white" />
          )}
        </div>
      </div>
    </Link>
  );
}
