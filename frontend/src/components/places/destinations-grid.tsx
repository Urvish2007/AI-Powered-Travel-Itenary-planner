"use client";

import { useQuery } from "@tanstack/react-query";
import { Compass, MapPin, Search, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { CityBackdrop } from "@/components/places/city-backdrop";
import {
  EmptyState,
  Input,
  Rating,
  SectionHeading,
  Skeleton,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { hasCityPhoto } from "@/lib/images";
import { normalizeSearch, pluralize } from "@/lib/utils";

export function DestinationsGrid() {
  const [query, setQuery] = useState("");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["cities"],
    queryFn: () => api.places.cities(),
    staleTime: 10 * 60_000,
  });

  const cities = useMemo(() => {
    const needle = normalizeSearch(query.trim());
    return (data ?? [])
      .filter(
        (city) =>
          !needle ||
          normalizeSearch(city.city).includes(needle) ||
          normalizeSearch(city.country).includes(needle),
      )
      // Cities with a real photograph lead; the rest follow alphabetically.
      .sort(
        (a, b) =>
          Number(hasCityPhoto(b.city)) - Number(hasCityPhoto(a.city)) ||
          a.city.localeCompare(b.city),
      );
  }, [data, query]);

  return (
    <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8 lg:py-16">
      <SectionHeading
        eyebrow="Destinations"
        title="Where we can plan for you"
        description="Each city has a curated shortlist of its best-known places, with coordinates and typical visit lengths. That is what lets the planner schedule a day rather than just suggest ideas."
      />

      {!isLoading && !isError && (
        <div className="mx-auto mt-10 max-w-xl">
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={`Search ${data?.length ?? ""} cities or countries`}
              className="pl-11 pr-11"
              aria-label="Search destinations"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                aria-label="Clear search"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <p className="mt-3 text-center text-sm text-muted-foreground" aria-live="polite">
            {cities.length} {cities.length === 1 ? "destination" : "destinations"}
            {query.trim() && ` matching “${query.trim()}”`}
          </p>
        </div>
      )}

      {isLoading ? (
        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-80 rounded-2xl" />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          className="mt-12"
          icon={<Compass />}
          title="Could not load destinations"
          description="The server did not respond. Refresh the page to try again."
        />
      ) : (
        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {cities.map((city, index) => {
            return (
              <Link
                key={`${city.city}-${city.country}`}
                href={`/destinations/${encodeURIComponent(city.city.toLowerCase())}`}
                className="group relative block h-80 overflow-hidden rounded-2xl shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <CityBackdrop
                  city={city.city}
                  country={city.country}
                  cityPhoto={city.photo}
                  priority={index < 3}
                  sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
                  className="object-cover transition-transform duration-700 group-hover:scale-105"
                />
                <div className="absolute inset-0 scrim" />
                <div className="absolute inset-x-0 bottom-0 p-6 text-white">
                  <p className="flex items-center gap-1.5 text-sm text-white/80">
                    <MapPin className="size-3.5" />
                    {city.country}
                  </p>
                  <h2 className="mt-1 text-2xl font-bold">{city.city}</h2>
                  <div className="mt-3 flex items-center justify-between">
                    <span className="text-sm text-white/85">
                      {city.place_count} {pluralize(city.place_count, "place")}
                    </span>
                    {city.average_rating > 0 && (
                      <Rating value={city.average_rating} className="text-white" />
                    )}
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      {!isLoading && !isError && cities.length === 0 && (
        <EmptyState
          className="mt-8"
          icon={<Search />}
          title={`No destination matches “${query.trim()}”`}
          description="Try another spelling, or search by country."
        />
      )}

      <p className="mt-12 rounded-2xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
        Opening hours and prices for most destinations are estimates based on
        the type of place, so check a venue before you go. More cities are
        being added.
      </p>
    </div>
  );
}
