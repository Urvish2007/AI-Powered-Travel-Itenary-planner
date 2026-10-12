"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Route, Timer } from "lucide-react";
import { useMemo, useState } from "react";

import { ItineraryMap, type MapStop } from "@/components/map/itinerary-map";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/overlays";
import { Badge } from "@/components/ui/primitives";
import { ApiError, api } from "@/lib/api";
import { cn, formatDistance } from "@/lib/utils";
import type {
  Activity,
  AlgorithmRun,
  ItineraryDay,
  TransportMode,
} from "@/types/api";

/** The backend rejects more than 12 places per day. */
const MAX_PLACES_PER_DAY = 12;
const DEFAULT_DAY_START = "09:00";
const DEFAULT_DAY_END = "21:00";

/**
 * An activity the route solver can actually plan around.
 *
 * It needs a `place_id` (the solver works from stored places, not free text) and
 * a `location` (coordinates to route between). Meals inserted into gaps have
 * both; a "free time" block has neither.
 */
type EligibleActivity = Activity & {
  place_id: string;
  location: NonNullable<Activity["location"]>;
};

function isEligible(activity: Activity): activity is EligibleActivity {
  return Boolean(activity.place_id) && activity.location != null;
}

/**
 * Compare how six routing algorithms would order one day's stops.
 *
 * This is a read-only explainer, not an editor: it shows the trade-off between
 * plan quality and compute time on the user's own data. Nothing it displays
 * changes the saved itinerary.
 */
export function RouteLab({
  day,
  transportMode,
  className,
}: {
  day: ItineraryDay;
  transportMode?: TransportMode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  // null means "follow the best run". Derived rather than synced in an effect,
  // so a new set of results cannot leave a stale selection behind.
  const [chosen, setChosen] = useState<string | null>(null);

  const eligible = useMemo(
    () => day.activities.filter(isEligible),
    [day.activities],
  );

  const compare = useMutation({
    mutationFn: () =>
      api.itineraries.compareAlgorithms({
        place_ids: eligible.map((activity) => activity.place_id),
        // GeoJSON stores [longitude, latitude] — index 1 is the latitude.
        start_latitude: eligible[0].location.coordinates[1],
        start_longitude: eligible[0].location.coordinates[0],
        days: 1,
        max_places_per_day: Math.min(MAX_PLACES_PER_DAY, eligible.length),
        ...dayWindow(day.activities),
        // Opening hours are checked against this date's weekday, so a day that
        // falls on a Monday is planned around Monday closures.
        start_date: day.day_date,
        transport_mode: transportMode ?? "walking",
        time_limit_seconds: 3,
      }),
  });

  const runs = compare.data?.runs ?? [];
  const bestAlgorithm = compare.data?.best_algorithm;
  const selectedKey = chosen ?? bestAlgorithm ?? null;
  const selected = runs.find((run) => run.algorithm === selectedKey) ?? null;

  const mapStops = useMemo(
    () => (selected ? runToMapStops(selected, eligible) : []),
    [selected, eligible],
  );

  // Fewer than two stops is not a route; there is nothing to compare.
  if (eligible.length < 2) return null;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) return;
    // Fired from the user's click, not from an effect, so this never trips the
    // set-state-in-effect rule.
    setChosen(null);
    compare.mutate();
  }

  return (
    <div className={className}>
      <Button
        variant="outline"
        size="sm"
        full
        onClick={() => handleOpenChange(true)}
      >
        <Route />
        Compare route algorithms
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent sheetOnMobile className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>How else could this day be ordered?</DialogTitle>
            <DialogDescription>
              Six algorithms plan day {day.day_number}&rsquo;s {eligible.length}{" "}
              stops from the same starting point. This is a comparison only —
              your itinerary is not changed.
            </DialogDescription>
          </DialogHeader>

          {compare.isPending ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16">
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Solving the same day six ways…
              </p>
            </div>
          ) : compare.isError ? (
            <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/10 p-4">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-destructive">
                  Could not compare the algorithms
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {compare.error instanceof ApiError
                    ? compare.error.message
                    : "Something went wrong. Please try again."}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => compare.mutate()}
                >
                  Try again
                </Button>
              </div>
            </div>
          ) : runs.length > 0 ? (
            <div className="space-y-4">
              <ResultsTable
                runs={runs}
                selectedKey={selectedKey}
                onSelect={setChosen}
              />

              {mapStops.length > 0 && (
                <div className="h-56 sm:h-64">
                  <ItineraryMap
                    stops={mapStops}
                    distanceKm={selected?.total_distance_km}
                    label={`Route planned by ${selected?.label ?? "the selected algorithm"}`}
                  />
                </div>
              )}

              <p className="text-xs leading-relaxed text-muted-foreground">
                Cost = minutes of travel and visiting, plus a penalty for every
                place left out. Lower is better.
              </p>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ResultsTable({
  runs,
  selectedKey,
  onSelect,
}: {
  runs: AlgorithmRun[];
  selectedKey: string | null;
  onSelect: (algorithm: string) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Algorithm results — select one to see its route"
      className="overflow-hidden rounded-xl border border-border"
    >
      {/* Column headings. Hidden from assistive tech because each row already
          labels its own values; this is a visual guide only. */}
      <div
        aria-hidden
        className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-secondary/50 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground sm:grid-cols-[1.4fr_repeat(4,auto)]"
      >
        <span>Algorithm</span>
        <span className="text-right">Cost</span>
        <span className="hidden text-right sm:block">Distance</span>
        <span className="hidden text-right sm:block">Time</span>
        <span className="text-right">Gap</span>
      </div>

      <ul className="divide-y divide-border">
        {runs.map((run) => {
          const selectable = run.scheduled_count > 0;
          const isSelected = run.algorithm === selectedKey;

          return (
            <li key={run.algorithm}>
              <button
                type="button"
                aria-pressed={isSelected}
                disabled={!selectable}
                onClick={() => onSelect(run.algorithm)}
                className={cn(
                  "grid w-full grid-cols-[1fr_auto_auto] items-center gap-3 px-4 py-3 text-left text-sm transition-colors",
                  "sm:grid-cols-[1.4fr_repeat(4,auto)]",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  isSelected ? "bg-primary-soft" : "hover:bg-secondary",
                  !selectable && "cursor-not-allowed opacity-55",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium">{run.label}</span>
                  {run.dropped_count > 0 && (
                    <span className="block text-xs text-warning-foreground dark:text-warning">
                      skips {run.dropped_count}
                    </span>
                  )}
                  {!selectable && (
                    <span className="block text-xs text-muted-foreground">
                      no feasible plan
                    </span>
                  )}
                </span>

                <span className="text-right font-semibold tabular-nums">
                  {selectable ? `${run.objective} min` : "—"}
                </span>

                <span className="hidden text-right tabular-nums text-muted-foreground sm:block">
                  {formatDistance(run.total_distance_km)}
                </span>

                <span className="hidden items-center justify-end gap-1 text-right tabular-nums text-muted-foreground sm:flex">
                  <Timer className="size-3.5" />
                  {run.duration_seconds.toFixed(2)}s
                </span>

                <span className="text-right">
                  {run.is_best ? (
                    <Badge variant="success" size="sm">
                      Best
                    </Badge>
                  ) : selectable ? (
                    <span className="tabular-nums text-muted-foreground">
                      +{run.gap_percent.toFixed(1)}%
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Turn an algorithm's plan into map pins.
 *
 * The run carries stop names and times but not coordinates, so each stop is
 * matched back to the day's activity by `place_id` to recover its position.
 */
function runToMapStops(
  run: AlgorithmRun,
  eligible: EligibleActivity[],
): MapStop[] {
  const byPlaceId = new Map(
    eligible.map((activity) => [activity.place_id, activity]),
  );

  return run.stops.flatMap((stop, index) => {
    const activity = byPlaceId.get(stop.place_id);
    if (!activity) return [];
    return [
      {
        title: stop.name,
        latitude: activity.location.coordinates[1],
        longitude: activity.location.coordinates[0],
        startTime: stop.arrival_time,
        order: index + 1,
        kind: activity.kind,
      },
    ];
  });
}

/**
 * The hours the day actually occupies, as the solver's window.
 *
 * "HH:MM" strings sort correctly as text, so no parsing is needed. An empty or
 * inverted range falls back to a sensible default rather than handing the
 * backend a window it would reject.
 */
function dayWindow(activities: Activity[]): {
  day_start_time: string;
  day_end_time: string;
} {
  if (activities.length === 0) {
    return { day_start_time: DEFAULT_DAY_START, day_end_time: DEFAULT_DAY_END };
  }

  const start = activities.reduce(
    (earliest, activity) =>
      activity.start_time < earliest ? activity.start_time : earliest,
    activities[0].start_time,
  );
  const end = activities.reduce(
    (latest, activity) =>
      activity.end_time > latest ? activity.end_time : latest,
    activities[0].end_time,
  );

  if (end <= start) {
    return { day_start_time: DEFAULT_DAY_START, day_end_time: DEFAULT_DAY_END };
  }
  return { day_start_time: start, day_end_time: end };
}
