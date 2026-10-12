"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CalendarDays,
  Compass,
  MapPin,
  Plus,
  Sparkles,
  Users,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Badge,
  Card,
  EmptyState,
  Skeleton,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { blurPhoto, cityImage, photo } from "@/lib/images";
import { formatDateRange, formatRelative, pluralize } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";
import type { Trip, TripStatus } from "@/types/api";

const STATUS_STYLE: Record<
  TripStatus,
  { label: string; variant: "default" | "primary" | "success" | "warning" }
> = {
  draft: { label: "Draft", variant: "default" },
  planning: { label: "Planning", variant: "warning" },
  ready: { label: "Ready", variant: "success" },
  archived: { label: "Archived", variant: "default" },
};

export function TripsDashboard() {
  const user = useAuthStore((s) => s.user);

  const tripsQuery = useQuery({
    queryKey: ["trips"],
    queryFn: () => api.trips.list({ limit: 50 }),
  });

  const statsQuery = useQuery({
    queryKey: ["user-stats"],
    queryFn: () => api.users.stats(),
  });

  const trips = tripsQuery.data?.items ?? [];
  const firstName = user?.full_name.split(" ")[0] ?? "there";

  return (
    <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8 lg:py-14">
      <header className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-3xl font-extrabold sm:text-4xl">
            Hello, {firstName}
          </h1>
          <p className="mt-2 text-muted-foreground">
            {trips.length === 0
              ? "You have not planned anything yet."
              : `${trips.length} ${pluralize(trips.length, "trip")} · ${
                  statsQuery.data?.itineraries ?? 0
                } ${pluralize(statsQuery.data?.itineraries ?? 0, "itinerary", "itineraries")} built`}
          </p>
        </div>
        <Button asChild size="lg">
          <Link href="/plan">
            <Plus />
            Plan a new trip
          </Link>
        </Button>
      </header>

      {tripsQuery.isLoading ? (
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-72 rounded-2xl" />
          ))}
        </div>
      ) : tripsQuery.isError ? (
        <EmptyState
          className="mt-10"
          icon={<Compass />}
          title="Could not load your trips"
          description="The server did not respond. Refresh the page to try again."
        />
      ) : trips.length === 0 ? (
        <EmptyState
          className="mt-10"
          icon={<Sparkles />}
          title="Your first trip starts here"
          description="Tell us where you're going and how you like to travel. We'll handle the timings, the opening hours and the walking."
          action={
            <Button asChild size="lg">
              <Link href="/plan">
                Plan a trip
                <ArrowRight />
              </Link>
            </Button>
          }
        />
      ) : (
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {trips.map((trip) => (
            <TripCard key={trip.id} trip={trip} />
          ))}
        </div>
      )}
    </div>
  );
}

function TripCard({ trip }: { trip: Trip }) {
  const imageKey = cityImage(trip.destination.city);
  const status = STATUS_STYLE[trip.status];
  const travellers =
    trip.party.adults + trip.party.children + trip.party.infants;

  return (
    <Card
      interactive
      className="group relative overflow-hidden focus-within:ring-2 focus-within:ring-ring"
    >
      <div className="relative h-40">
        <Image
          src={photo(imageKey, { width: 600, height: 400 })}
          alt=""
          fill
          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
          placeholder="blur"
          blurDataURL={blurPhoto(imageKey)}
          className="object-cover transition-transform duration-500 group-hover:scale-105"
        />
        <div className="absolute inset-0 scrim" />
        <Badge variant={status.variant} className="absolute right-3 top-3">
          {status.label}
        </Badge>
        <div className="absolute inset-x-0 bottom-0 p-4">
          <p className="flex items-center gap-1.5 text-xs font-medium text-white/80">
            <MapPin className="size-3" />
            {trip.destination.city}, {trip.destination.country}
          </p>
        </div>
      </div>

      <div className="p-5">
        {/* The whole card is the link target, via the stretched pseudo-element,
            so the tap area is the card rather than just the title. */}
        <h2 className="font-semibold leading-snug">
          <Link
            href={`/trips/${trip.id}`}
            className="outline-none after:absolute after:inset-0 after:content-['']"
          >
            {trip.title}
          </Link>
        </h2>

        <dl className="mt-3 space-y-1.5 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <dt className="sr-only">Dates</dt>
            <CalendarDays className="size-4 shrink-0" />
            <dd>
              {formatDateRange(trip.start_date, trip.end_date)} ·{" "}
              {trip.duration_days} {pluralize(trip.duration_days, "day")}
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <dt className="sr-only">Travellers</dt>
            <Users className="size-4 shrink-0" />
            <dd>
              {travellers} {pluralize(travellers, "traveller")}
            </dd>
          </div>
        </dl>

        <p className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
          Updated {formatRelative(trip.updated_at)}
        </p>
      </div>
    </Card>
  );
}
