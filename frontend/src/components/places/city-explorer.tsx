"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, LayoutGrid, Map as MapIcon, Search, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { PlacesMap } from "@/components/map/places-map";
import { PlaceCard, PlaceCardSkeleton } from "@/components/places/place-card";
import { Button } from "@/components/ui/button";
import {
  Badge,
  EmptyState,
  Input,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn, titleCase } from "@/lib/utils";
import type { PlaceCategory } from "@/types/api";

const CATEGORIES: (PlaceCategory | "all")[] = [
  "all",
  "landmark",
  "museum",
  "park",
  "restaurant",
  "cafe",
  "shopping",
  "religious",
  "viewpoint",
  "nightlife",
];

/**
 * Browse the places we hold for one city.
 *
 * Two search modes are offered side by side because they answer different
 * questions: keyword search finds a place you can already name, semantic search
 * finds one you can only describe ("somewhere quiet to sit"). Keeping both
 * visible is clearer than guessing which the user meant.
 */
export function CityExplorer({ city }: { city: string }) {
  const [query, setQuery] = useState("");
  const [semantic, setSemantic] = useState(false);
  const [category, setCategory] = useState<PlaceCategory | "all">("all");
  const [view, setView] = useState<"cards" | "map">("cards");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ["places", city, category],
    queryFn: () =>
      api.places.list({
        city,
        category: category === "all" ? undefined : category,
        limit: 60,
      }),
  });

  const searchQuery = useQuery({
    queryKey: ["place-search", city, query, semantic],
    queryFn: () =>
      semantic
        ? api.places.semanticSearch({ query, city, limit: 24 })
        : api.places.search({ q: query, city, limit: 24 }),
    // Two characters is the server's minimum; querying below it only produces
    // a 422 the user cannot act on.
    enabled: query.trim().length >= 2,
  });

  const searching = query.trim().length >= 2;
  const places = useMemo(() => {
    if (searching) return searchQuery.data ?? [];
    return listQuery.data?.items ?? [];
  }, [searching, searchQuery.data, listQuery.data]);

  const loading = searching ? searchQuery.isLoading : listQuery.isLoading;
  const selectedPlace = places.find((place) => place.id === selectedId) ?? null;

  return (
    <div className="space-y-8">
      {/* Search */}
      <div className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={
              semantic
                ? "Describe what you want — “somewhere quiet with a view”"
                : `Search places in ${city}`
            }
            className="pl-11 pr-11"
            aria-label={`Search places in ${city}`}
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

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setSemantic((value) => !value)}
            aria-pressed={semantic}
            className={cn(
              "inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-xs font-medium transition-all",
              semantic
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border text-muted-foreground hover:border-foreground/25",
            )}
          >
            <Sparkles className="size-3.5" />
            Search by meaning
          </button>
          {semantic && (
            <span className="text-xs text-muted-foreground">
              Matches what a place is like, not just its name
            </span>
          )}
        </div>
      </div>

      {/* Category filter — hidden while searching, where it would fight the query */}
      {!searching && (
        <div className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:px-0">
          {CATEGORIES.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setCategory(value)}
              aria-pressed={category === value}
              className={cn(
                "shrink-0 rounded-full border px-4 py-2 text-sm font-medium transition-all",
                category === value
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border hover:border-foreground/25 hover:bg-secondary",
              )}
            >
              {value === "all" ? "Everything" : titleCase(value)}
            </button>
          ))}
        </div>
      )}

      {/* Results */}
      {loading ? (
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <PlaceCardSkeleton key={index} />
          ))}
        </div>
      ) : places.length === 0 ? (
        <EmptyState
          icon={<Search />}
          title={searching ? `Nothing matched “${query}”` : "Nothing here yet"}
          description={
            searching
              ? semantic
                ? "Try describing it differently, or switch off search by meaning."
                : "Try fewer words, or switch on search by meaning."
              : "We have not added places in this category yet."
          }
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {places.length} {places.length === 1 ? "place" : "places"}
              {searching && ` matching “${query}”`}
            </p>
            <div
              role="group"
              aria-label="How to show places"
              className="flex overflow-hidden rounded-lg border border-border text-sm font-medium"
            >
              {(
                [
                  ["cards", "Cards", LayoutGrid],
                  ["map", "Map", MapIcon],
                ] as const
              ).map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setView(value)}
                  aria-pressed={view === value}
                  className={cn(
                    "inline-flex items-center gap-2 px-3.5 py-2 transition-colors",
                    view === value
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {view === "cards" ? (
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {places.map((place, index) => (
                <PlaceCard key={place.id} place={place} priority={index < 3} />
              ))}
            </div>
          ) : (
            <div className="space-y-5">
              <PlacesMap
                places={places}
                selectedId={selectedId}
                onSelect={setSelectedId}
                className="h-[26rem] sm:h-[34rem]"
                label={`Map of places in ${city}`}
              />
              {selectedPlace ? (
                <div className="mx-auto max-w-md">
                  <PlaceCard place={selectedPlace} />
                </div>
              ) : (
                <p className="text-center text-sm text-muted-foreground">
                  Select a pin to see the place.
                </p>
              )}
            </div>
          )}
        </>
      )}

      {/* Conversion prompt, placed after the browsing is done. */}
      <div className="rounded-3xl border border-border bg-secondary/50 px-6 py-10 text-center">
        <Badge variant="primary">
          <Sparkles />
          Ready when you are
        </Badge>
        <h2 className="mt-4 text-2xl font-bold">
          Turn {city} into a day-by-day plan
        </h2>
        <p className="mx-auto mt-2 max-w-md text-muted-foreground">
          We&apos;ll pick from these places, schedule them around opening hours and
          keep the walking inside your limit.
        </p>
        <Button asChild size="lg" className="mt-6">
          <Link href={`/plan?destination=${encodeURIComponent(city)}`}>
            Plan a trip to {city}
            <ArrowRight />
          </Link>
        </Button>
      </div>
    </div>
  );
}
