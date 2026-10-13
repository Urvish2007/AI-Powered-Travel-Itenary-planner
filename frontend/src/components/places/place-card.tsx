"use client";

import { Accessibility, Clock, MapPin, Users } from "lucide-react";
import Image from "next/image";

import { Badge, Card, Rating } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { blurPhoto, categoryImage, placeImage } from "@/lib/images";
import { cn, formatDistance, formatDuration, formatMoney, titleCase } from "@/lib/utils";
import type { Place } from "@/types/api";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The days a place is shut, phrased for a human. */
function closedDays(place: Place): string | null {
  const closed = place.opening_hours
    .filter((window) => window.closed)
    .map((window) => WEEKDAYS[window.day_of_week])
    .filter(Boolean);
  if (closed.length === 0) return null;
  return `Closed ${closed.join(", ")}`;
}

export function PlaceCard({
  place,
  priority,
  className,
}: {
  place: Place;
  priority?: boolean;
  className?: string;
}) {
  const imageKey = categoryImage(place.category);
  const src = placeImage(place, { width: 600, height: 400 });
  const closed = closedDays(place);

  return (
    <Card interactive className={cn("overflow-hidden", className)}>
      <div className="relative h-44">
        <Image
          src={src}
          alt={place.name}
          fill
          priority={priority}
          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
          placeholder="blur"
          blurDataURL={blurPhoto(imageKey)}
          className="object-cover"
          // A place's own image_url can 404; the category photo is the backstop.
          unoptimized={Boolean(place.image_url)}
        />
        <div className="absolute inset-0 scrim" />

        <Badge variant="glass" className="absolute left-3 top-3">
          {titleCase(place.category)}
        </Badge>

        {place.distance_km != null && (
          <Badge variant="glass" className="absolute right-3 top-3">
            <MapPin />
            {formatDistance(place.distance_km)}
          </Badge>
        )}

        <div className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 p-4">
          <h3 className="text-lg font-bold leading-tight text-white">
            {place.name}
          </h3>
          {place.rating > 0 && (
            <Rating
              value={place.rating}
              reviewCount={place.review_count}
              className="shrink-0 text-white"
            />
          )}
        </div>
      </div>

      <div className="p-5">
        {place.description && (
          <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground">
            {place.description}
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Badge variant="outline" size="sm">
            <Clock />
            {formatDuration(place.average_visit_minutes)}
          </Badge>
          <Badge variant={place.cost.is_free ? "success" : "outline"} size="sm">
            {place.cost.is_free
              ? "Free"
              : formatMoney(place.cost.amount, place.cost.currency)}
          </Badge>
          {place.wheelchair_accessible && (
            <Tooltip content="Step-free access">
              <span>
                <Badge variant="primary" size="sm">
                  <Accessibility />
                  Accessible
                </Badge>
              </span>
            </Tooltip>
          )}
          {place.family_friendly && (
            <Badge variant="outline" size="sm">
              <Users />
              Family
            </Badge>
          )}
        </div>

        {closed && (
          <p className="mt-3 text-xs font-medium text-warning-foreground dark:text-warning">
            {closed}
          </p>
        )}

        {place.tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {place.tags.slice(0, 4).map((tag) => (
              <span
                key={tag}
                className="rounded-full bg-secondary px-2.5 py-1 text-[11px] font-medium text-muted-foreground"
              >
                {titleCase(tag)}
              </span>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

export function PlaceCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-2xl border border-border">
      <div className="skeleton h-44" />
      <div className="space-y-3 p-5">
        <div className="skeleton h-4 w-full rounded" />
        <div className="skeleton h-4 w-2/3 rounded" />
        <div className="flex gap-2">
          <div className="skeleton h-6 w-20 rounded-full" />
          <div className="skeleton h-6 w-16 rounded-full" />
        </div>
      </div>
    </div>
  );
}
