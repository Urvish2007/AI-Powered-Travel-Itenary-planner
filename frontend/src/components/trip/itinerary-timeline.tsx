"use client";

import {
  Bike,
  Bus,
  Car,
  Clock,
  Footprints,
  MapPin,
  Sparkles,
  Ticket,
  Trash2,
  Utensils,
} from "lucide-react";
import Image from "next/image";

import { Badge, Card } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { blurPhoto, categoryImage, photo } from "@/lib/images";
import {
  cn,
  formatDate,
  formatDistance,
  formatDuration,
  formatMoney,
  formatTime,
} from "@/lib/utils";
import type { Activity, ItineraryDay, TransportMode } from "@/types/api";

const TRANSPORT_ICON: Record<TransportMode, typeof Footprints> = {
  walking: Footprints,
  cycling: Bike,
  transit: Bus,
  driving: Car,
};

const TRANSPORT_LABEL: Record<TransportMode, string> = {
  walking: "Walk",
  cycling: "Cycle",
  transit: "Transit",
  driving: "Drive",
};

/** A travel leg, rendered between two activities rather than as a card. */
function TravelConnector({ activity }: { activity: Activity }) {
  const leg = activity.travel_from_previous;
  if (!leg || leg.distance_km <= 0) return null;

  const Icon = TRANSPORT_ICON[leg.mode] ?? Footprints;
  return (
    <div className="flex items-center gap-3 py-1 pl-[4.5rem] sm:pl-24">
      <span className="flex items-center gap-2 rounded-full bg-secondary px-3 py-1.5 text-xs font-medium text-muted-foreground">
        <Icon className="size-3.5" />
        {TRANSPORT_LABEL[leg.mode] ?? "Travel"} {formatDistance(leg.distance_km)}
        <span aria-hidden>·</span>
        {formatDuration(leg.duration_minutes)}
      </span>
    </div>
  );
}

function ActivityCard({
  activity,
  index,
  dayNumber,
  currency,
  onRemove,
  readOnly,
}: {
  activity: Activity;
  index: number;
  dayNumber: number;
  currency: string;
  onRemove?: (dayNumber: number, index: number) => void;
  readOnly?: boolean;
}) {
  const isMeal = activity.kind === "meal";
  const imageKey = categoryImage(isMeal ? "restaurant" : "attraction");

  return (
    <li className="relative flex gap-3 sm:gap-5">
      {/* Time gutter + spine */}
      <div className="flex w-14 shrink-0 flex-col items-end pt-4 sm:w-20">
        <time className="text-sm font-semibold tabular-nums">
          {formatTime(activity.start_time)}
        </time>
        <span className="text-xs text-muted-foreground tabular-nums">
          {formatTime(activity.end_time)}
        </span>
      </div>

      <div className="relative flex flex-col items-center pt-5">
        <span
          className={cn(
            "z-10 size-3 rounded-full ring-4 ring-background",
            isMeal ? "bg-accent" : "bg-primary",
          )}
        />
        <span className="absolute top-5 h-full w-px bg-border" aria-hidden />
      </div>

      <Card className="mb-3 flex-1 overflow-hidden" interactive>
        <div className="flex gap-4 p-4">
          <div className="relative hidden size-20 shrink-0 overflow-hidden rounded-xl sm:block">
            <Image
              src={photo(imageKey, { width: 200, height: 200 })}
              alt=""
              fill
              sizes="80px"
              placeholder="blur"
              blurDataURL={blurPhoto(imageKey)}
              className="object-cover"
            />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h4 className="truncate font-semibold">{activity.title}</h4>
                {activity.address && (
                  <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground">
                    <MapPin className="size-3 shrink-0" />
                    {activity.address}
                  </p>
                )}
              </div>

              {!readOnly && onRemove && (
                <Tooltip content="Remove from this day">
                  <button
                    type="button"
                    onClick={() => onRemove(dayNumber, index)}
                    className="shrink-0 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                    aria-label={`Remove ${activity.title}`}
                  >
                    <Trash2 className="size-4" />
                  </button>
                </Tooltip>
              )}
            </div>

            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <Badge variant={isMeal ? "accent" : "primary"} size="sm">
                {isMeal ? <Utensils /> : <Ticket />}
                {isMeal ? "Meal" : "Visit"}
              </Badge>
              <Badge variant="outline" size="sm">
                <Clock />
                {formatDuration(activity.duration_minutes)}
              </Badge>
              {activity.estimated_cost > 0 && (
                <Badge variant="outline" size="sm">
                  {formatMoney(activity.estimated_cost, currency)}
                </Badge>
              )}
              {activity.estimated_cost === 0 && (
                <Badge variant="success" size="sm">
                  Free
                </Badge>
              )}
            </div>

            {activity.description && (
              <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                {activity.description}
              </p>
            )}

            {/* Why this is here — the planner explaining itself. */}
            {activity.rationale && (
              <p className="mt-2.5 flex items-start gap-1.5 rounded-lg bg-primary-soft px-3 py-2 text-xs text-primary">
                <Sparkles className="mt-0.5 size-3 shrink-0" />
                {activity.rationale}
              </p>
            )}
          </div>
        </div>
      </Card>
    </li>
  );
}

export function DayTimeline({
  day,
  currency,
  onRemoveActivity,
  readOnly,
}: {
  day: ItineraryDay;
  currency: string;
  onRemoveActivity?: (dayNumber: number, index: number) => void;
  readOnly?: boolean;
}) {
  if (day.activities.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border px-6 py-14 text-center">
        <p className="font-medium">Nothing scheduled for this day</p>
        <p className="mt-1.5 text-sm text-muted-foreground">
          A deliberately open day — or nothing fitted the constraints. Try
          regenerating with a longer day or a faster pace.
        </p>
      </div>
    );
  }

  return (
    <div>
      <header className="mb-6">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="text-xl font-bold">
            {day.title || `Day ${day.day_number}`}
          </h3>
          <time className="text-sm text-muted-foreground">
            {formatDate(day.day_date, {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </time>
        </div>
        {day.summary && (
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {day.summary}
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <Badge variant="outline">
            <Ticket />
            {day.activity_count} stops
          </Badge>
          {day.total_distance_km > 0 && (
            <Badge variant="outline">
              <Footprints />
              {formatDistance(day.total_distance_km)}
            </Badge>
          )}
          {day.estimated_cost > 0 && (
            <Badge variant="outline">
              {formatMoney(day.estimated_cost, currency)}
            </Badge>
          )}
        </div>
      </header>

      <ol className="relative">
        {day.activities.map((activity, index) => (
          <div key={`${activity.title}-${activity.start_time}-${index}`}>
            <TravelConnector activity={activity} />
            <ActivityCard
              activity={activity}
              index={index}
              dayNumber={day.day_number}
              currency={currency}
              onRemove={onRemoveActivity}
              readOnly={readOnly}
            />
          </div>
        ))}
      </ol>
    </div>
  );
}

/** A compact day picker, used above the timeline. */
export function DaySelector({
  days,
  active,
  onSelect,
}: {
  days: ItineraryDay[];
  active: number;
  onSelect: (dayNumber: number) => void;
}) {
  return (
    <div
      className="no-scrollbar snap-rail -mx-4 flex gap-2.5 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0"
      role="tablist"
      aria-label="Trip days"
    >
      {days.map((day) => {
        const selected = day.day_number === active;
        return (
          <button
            key={day.day_number}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onSelect(day.day_number)}
            className={cn(
              "shrink-0 rounded-2xl border px-4 py-3 text-left transition-all",
              selected
                ? "border-primary bg-primary text-primary-foreground shadow-sm"
                : "border-border hover:border-foreground/25 hover:bg-secondary",
            )}
          >
            <p
              className={cn(
                "text-xs font-medium",
                selected ? "text-primary-foreground/75" : "text-muted-foreground",
              )}
            >
              Day {day.day_number}
            </p>
            <p className="mt-0.5 text-sm font-semibold">
              {formatDate(day.day_date, { day: "numeric", month: "short" })}
            </p>
            <p
              className={cn(
                "mt-0.5 text-xs",
                selected ? "text-primary-foreground/75" : "text-muted-foreground",
              )}
            >
              {day.activity_count} stops
            </p>
          </button>
        );
      })}
    </div>
  );
}
