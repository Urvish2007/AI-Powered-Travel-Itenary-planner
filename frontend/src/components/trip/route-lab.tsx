"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Info, Loader2, Route, Timer } from "lucide-react";
import { useMemo, useRef, useState } from "react";

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
import { cn, formatDistance, formatDuration, pluralize } from "@/lib/utils";
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
 * Below this, comparing is theatre.
 *
 * With two stops every algorithm returns the same single ordering, so a table
 * of identical rows would imply a choice that does not exist.
 */
const MIN_PLACES_TO_COMPARE = 3;

/**
 * How long to wait before giving up on the comparison.
 *
 * The server caps its own solver at `time_limit_seconds` per algorithm, so this
 * only has to cover that plus transit. It exists so a stalled request fails
 * with an explanation instead of spinning forever.
 */
const COMPARE_TIMEOUT_MS = 20_000;

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
  onApply,
}: {
  day: ItineraryDay;
  transportMode?: TransportMode;
  className?: string;
  /**
   * Write a run's order onto the day. Omitted when the day is read-only (a
   * shared itinerary), in which case no "Use this route" button is offered.
   *
   * Rejecting with a 409 `ApiError` means the day changed since the comparison;
   * the dialog offers to re-run it rather than retrying a choice that no longer
   * applies.
   */
  onApply?: (run: AlgorithmRun) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  // null means "follow the best run". Derived rather than synced in an effect,
  // so a new set of results cannot leave a stale selection behind.
  const [chosen, setChosen] = useState<string | null>(null);

  const eligible = useMemo(
    () => day.activities.filter(isEligible),
    [day.activities],
  );

  // The run in flight, so closing the dialog can call off work nobody is
  // waiting for any more.
  const inFlight = useRef<AbortController | null>(null);

  const compare = useMutation({
    mutationFn: async () => {
      // Supersede anything still running, so a quick close-and-reopen cannot
      // leave two comparisons racing to fill the same table.
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;

      const timeout = setTimeout(() => {
        controller.abort(
          new DOMException("The comparison timed out.", "TimeoutError"),
        );
      }, COMPARE_TIMEOUT_MS);

      try {
        return await api.itineraries.compareAlgorithms(
          {
            place_ids: eligible.map((activity) => activity.place_id),
            // GeoJSON stores [longitude, latitude] — index 1 is the latitude.
            start_latitude: eligible[0].location.coordinates[1],
            start_longitude: eligible[0].location.coordinates[0],
            days: 1,
            max_places_per_day: Math.min(MAX_PLACES_PER_DAY, eligible.length),
            ...dayWindow(day.activities),
            // Opening hours are checked against this date's weekday, so a day
            // that falls on a Monday is planned around Monday closures.
            start_date: day.day_date,
            transport_mode: transportMode ?? "walking",
            time_limit_seconds: 3,
          },
          controller.signal,
        );
      } finally {
        clearTimeout(timeout);
      }
    },
  });

  const apply = useMutation({
    mutationFn: async (run: AlgorithmRun) => {
      if (!onApply) throw new Error("This itinerary cannot be edited.");
      await onApply(run);
    },
    onSuccess: () => setOpen(false),
  });

  // A 409 means the day moved on underneath the comparison, so the right offer
  // is to compare again rather than to retry a choice that no longer applies.
  const isStale =
    apply.error instanceof ApiError && apply.error.status === 409;

  const runs = compare.data?.runs ?? [];
  const bestAlgorithm = compare.data?.best_algorithm;
  const selectedKey = chosen ?? bestAlgorithm ?? null;
  const selected = runs.find((run) => run.algorithm === selectedKey) ?? null;

  const mapStops = useMemo(
    () => (selected ? runToMapStops(selected, eligible) : []),
    [selected, eligible],
  );

  // The order already saved on the day, drawn dashed under whichever plan is
  // selected. Without it the preview is a route with nothing to be better than.
  const savedStops = useMemo<MapStop[]>(
    () =>
      eligible.map((activity, index) => ({
        title: activity.title,
        latitude: activity.location.coordinates[1],
        longitude: activity.location.coordinates[0],
        startTime: activity.start_time,
        order: index + 1,
        kind: activity.kind,
      })),
    [eligible],
  );

  const enoughToCompare = eligible.length >= MIN_PLACES_TO_COMPARE;

  // No stop the solver could route between: there is nothing to offer, so the
  // button itself would be noise.
  if (eligible.length === 0) return null;

  function handleOpenChange(next: boolean) {
    setOpen(next);

    if (!next) {
      // Call off the request and drop its results. Reopening always starts a
      // fresh run, so no stale table or error can survive the close.
      inFlight.current?.abort();
      inFlight.current = null;
      compare.reset();
      return;
    }

    // Fired from the user's click, not from an effect, so this never trips the
    // set-state-in-effect rule.
    setChosen(null);
    apply.reset();
    if (enoughToCompare) compare.mutate();
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

      {/* handleOpenChange, not setOpen: Esc and the backdrop have to abort the
          request too, not just hide the dialog in front of it. */}
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent sheetOnMobile className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>How else could this day be ordered?</DialogTitle>
            <DialogDescription>
              Several algorithms plan day {day.day_number}&rsquo;s{" "}
              {eligible.length} stops from the same starting point. This is a
              comparison only — your itinerary is not changed.
            </DialogDescription>
          </DialogHeader>

          {!enoughToCompare ? (
            <div
              role="status"
              className="flex items-start gap-3 rounded-xl border border-border bg-secondary/40 p-4"
            >
              <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  Not enough stops to compare
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Day {day.day_number} has{" "}
                  {eligible.length === 1 ? "one routable stop" : "two routable stops"}
                  . Every algorithm returns the same order below{" "}
                  {MIN_PLACES_TO_COMPARE}, so there would be nothing to choose
                  between. Add another place with a location to compare.
                </p>
              </div>
            </div>
          ) : compare.isPending ? (
            <div
              role="status"
              className="flex flex-col items-center justify-center gap-3 py-16"
            >
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Solving the same day several ways…
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
                  {describeError(compare.error)}
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

              {/* Said every time, not only when measured: a figure that looks
                  precise reads as measured unless told otherwise. A response
                  without the field predates routing, so it is an estimate. */}
              <p className="text-xs text-muted-foreground">
                {compare.data?.estimated === false
                  ? `Distances and travel times measured on the road network (${compare.data.routing_provider}).`
                  : "Distances and travel times are estimated from straight-line distance, not measured routes."}
              </p>

              {mapStops.length > 0 && (
                <div className="h-56 sm:h-64">
                  <ItineraryMap
                    stops={mapStops}
                    baseline={savedStops}
                    baselineLabel="your current order"
                    distanceKm={selected?.total_distance_km}
                    label={`Route planned by ${selected?.label ?? "the selected algorithm"}, compared against your current order`}
                  />
                </div>
              )}

              {apply.isError && (
                <div
                  className={cn(
                    "flex items-start gap-3 rounded-xl border p-4",
                    isStale
                      ? "border-warning/40 bg-warning/10"
                      : "border-destructive/30 bg-destructive/10",
                  )}
                >
                  <AlertTriangle
                    className={cn(
                      "mt-0.5 size-4 shrink-0",
                      isStale ? "text-warning-foreground dark:text-warning" : "text-destructive",
                    )}
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">
                      {isStale
                        ? "This day changed while you were comparing"
                        : "Could not apply that route"}
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {describeError(apply.error)}
                    </p>
                    {isStale && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-3"
                        onClick={() => {
                          apply.reset();
                          setChosen(null);
                          compare.mutate();
                        }}
                      >
                        Compare again
                      </Button>
                    )}
                  </div>
                </div>
              )}

              {/* A plan that leaves stops out cannot be applied as-is: the
                  server keeps every place on the day, and putting the dropped
                  ones anywhere would be an order no algorithm chose. */}
              {onApply && selected && selected.dropped_count > 0 && (
                <p className="rounded-xl border border-border bg-secondary/40 p-4 text-sm text-muted-foreground">
                  {selected.label} leaves out{" "}
                  {selected.dropped_count}{" "}
                  {pluralize(selected.dropped_count, "stop")}, so it can&apos;t
                  be applied to this day. Pick a plan that keeps every stop.
                </p>
              )}

              {onApply && selected && selected.scheduled_count > 0 && selected.dropped_count === 0 && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-secondary/40 p-4">
                  <p className="min-w-0 text-sm text-muted-foreground">
                    Reorder day {day.day_number} to the{" "}
                    <span className="font-medium text-foreground">
                      {selected.label}
                    </span>{" "}
                    plan. Times and totals are recalculated.
                  </p>
                  <Button
                    size="sm"
                    onClick={() => apply.mutate(selected)}
                    isLoading={apply.isPending}
                    disabled={apply.isPending}
                  >
                    Use this route
                  </Button>
                </div>
              )}

              <p className="text-xs leading-relaxed text-muted-foreground">
                Score = minutes of travel and visiting, plus a penalty for every
                place left out. Lower is better, and it is the measure the
                algorithms are ranked on — so the shortest route is not always
                the best-scoring one.
              </p>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** One metric cell: label-above-value on mobile, bare value under a column heading on desktop. */
function Metric({
  label,
  value,
  sub,
  emphasis,
}: {
  label: string;
  value: string;
  sub?: string;
  emphasis?: boolean;
}) {
  return (
    <span className="min-w-0 sm:text-right">
      {/* The desktop layout has a heading row, so the inline label is mobile-only.
          aria-hidden because each row's own aria-label already names its values. */}
      <span
        aria-hidden
        className="block text-[11px] uppercase tracking-wide text-muted-foreground sm:hidden"
      >
        {label}
      </span>
      <span
        className={cn(
          "block truncate tabular-nums",
          emphasis ? "font-semibold" : "text-muted-foreground",
        )}
      >
        {value}
      </span>
      {sub && (
        <span className="block truncate text-xs tabular-nums text-muted-foreground">
          {sub}
        </span>
      )}
    </span>
  );
}

const COLUMNS = ["Algorithm", "Score", "Distance", "Travel", "Visits", "Stops"];

function ResultsTable({
  runs,
  selectedKey,
  onSelect,
}: {
  runs: AlgorithmRun[];
  selectedKey: string | null;
  onSelect: (algorithm: string) => void;
}) {
  // Which run wins on each measure. The backend marks the best score; distance
  // and travel time are derived here so the table can show when a different
  // algorithm wins on one of them — a plan can be shortest on the road and
  // still score worse once waiting and skipped places are counted.
  const bestScore = runs.find((run) => run.is_best)?.algorithm ?? null;
  const bestDistance = bestBy(runs, (run) => run.total_distance_km);
  const bestTravel = bestBy(runs, (run) => run.travel_minutes);

  return (
    <div
      role="group"
      aria-label="Algorithm results — select one to see its route"
      className="overflow-hidden rounded-xl border border-border"
    >
      {/* Visual column guide. Hidden on mobile, where each cell carries its own
          label, and hidden from assistive tech because every row is self-describing. */}
      <div
        aria-hidden
        className="hidden gap-3 border-b border-border bg-secondary/50 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground sm:grid sm:grid-cols-[1.5fr_repeat(5,minmax(0,auto))]"
      >
        {COLUMNS.map((column, index) => (
          <span key={column} className={index === 0 ? undefined : "text-right"}>
            {column}
          </span>
        ))}
      </div>

      <ul className="divide-y divide-border">
        {runs.map((run) => {
          const selectable = run.scheduled_count > 0;
          const isSelected = run.algorithm === selectedKey;
          const visitMinutes = visitMinutesOf(run);

          return (
            <li key={run.algorithm}>
              <button
                type="button"
                aria-pressed={isSelected}
                disabled={!selectable}
                onClick={() => onSelect(run.algorithm)}
                aria-label={describeRun(run, visitMinutes, {
                  bestScore,
                  bestDistance,
                  bestTravel,
                })}
                className={cn(
                  "grid w-full gap-x-3 gap-y-2 px-4 py-3 text-left text-sm transition-colors",
                  // Mobile: a card, two metrics per line. Desktop: one row.
                  "grid-cols-2 sm:grid-cols-[1.5fr_repeat(5,minmax(0,auto))] sm:items-center sm:gap-y-0",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  isSelected ? "bg-primary-soft" : "hover:bg-secondary",
                  !selectable && "cursor-not-allowed opacity-55",
                )}
              >
                <span className="col-span-2 min-w-0 sm:col-span-1">
                  <span className="block truncate font-medium">{run.label}</span>

                  <span className="mt-1 flex flex-wrap items-center gap-1">
                    {run.algorithm === bestScore && (
                      <Badge variant="success" size="sm">
                        Best overall
                      </Badge>
                    )}
                    {/* Only worth a badge when it is not the same run as the best
                        score; otherwise it is three badges saying one thing. */}
                    {run.algorithm === bestDistance &&
                      bestDistance !== bestScore && (
                        <Badge variant="primary" size="sm">
                          Shortest
                        </Badge>
                      )}
                    {run.algorithm === bestTravel && bestTravel !== bestScore && (
                      <Badge variant="accent" size="sm">
                        Least travel
                      </Badge>
                    )}
                  </span>

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
                  <span className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                    <Timer className="size-3" aria-hidden />
                    solved in {run.duration_seconds.toFixed(2)}s
                  </span>
                </span>

                <Metric
                  label="Score"
                  emphasis
                  value={selectable ? `${run.objective} min` : "—"}
                  sub={
                    selectable && run.algorithm !== bestScore
                      ? `+${run.gap_percent.toFixed(1)}%`
                      : undefined
                  }
                />
                <Metric
                  label="Distance"
                  value={selectable ? formatDistance(run.total_distance_km) : "—"}
                />
                <Metric
                  label="Travel"
                  value={selectable ? formatDuration(run.travel_minutes) : "—"}
                />
                <Metric
                  label="Visits"
                  value={selectable ? formatDuration(visitMinutes) : "—"}
                />
                <Metric
                  label="Stops"
                  value={selectable ? String(run.scheduled_count) : "—"}
                />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** What to tell the user went wrong, in their terms rather than the transport's. */
function describeError(error: unknown): string {
  if (error instanceof DOMException && error.name === "TimeoutError") {
    return `The comparison took longer than ${Math.round(COMPARE_TIMEOUT_MS / 1000)} seconds. The server may be busy — try again, or try a day with fewer stops.`;
  }
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Please try again.";
}

/** The algorithm of the feasible run with the lowest `measure`, or null if none is feasible. */
function bestBy(
  runs: AlgorithmRun[],
  measure: (run: AlgorithmRun) => number,
): string | null {
  const feasible = runs.filter((run) => run.scheduled_count > 0);
  if (feasible.length === 0) return null;
  return feasible.reduce((best, run) =>
    measure(run) < measure(best) ? run : best,
  ).algorithm;
}

function clockToMinutes(clock: string): number {
  const [hours, minutes] = clock.split(":").map(Number);
  if (Number.isNaN(hours) || Number.isNaN(minutes)) return 0;
  return hours * 60 + minutes;
}

/**
 * Minutes this plan spends actually at places, as opposed to getting between them.
 *
 * The run reports travel time directly but not visiting time, so it comes from
 * each stop's own arrival and departure.
 */
function visitMinutesOf(run: AlgorithmRun): number {
  return run.stops.reduce((total, stop) => {
    const span =
      clockToMinutes(stop.departure_time) - clockToMinutes(stop.arrival_time);
    // A stop that reads as ending before it starts has either crossed midnight
    // or is malformed. Either way it contributes nothing, never a negative.
    return total + (span > 0 ? span : 0);
  }, 0);
}

/** A row's accessible name: the numbers a sighted user reads across the row. */
function describeRun(
  run: AlgorithmRun,
  visitMinutes: number,
  best: { bestScore: string | null; bestDistance: string | null; bestTravel: string | null },
): string {
  if (run.scheduled_count === 0) {
    return `${run.label}: no feasible plan for this day`;
  }

  const wins = [
    run.algorithm === best.bestScore && "best overall",
    run.algorithm === best.bestDistance &&
      best.bestDistance !== best.bestScore &&
      "shortest distance",
    run.algorithm === best.bestTravel &&
      best.bestTravel !== best.bestScore &&
      "least travel time",
  ].filter((win): win is string => Boolean(win));

  return [
    `${run.label}: score ${run.objective} minutes`,
    run.algorithm === best.bestScore
      ? null
      : `${run.gap_percent.toFixed(1)} percent worse than the best`,
    `${formatDistance(run.total_distance_km)} of travel`,
    `${formatDuration(run.travel_minutes)} travelling`,
    `${formatDuration(visitMinutes)} visiting`,
    `${run.scheduled_count} stops`,
    run.dropped_count > 0 ? `${run.dropped_count} places skipped` : null,
    wins.length > 0 ? wins.join(", ") : null,
  ]
    .filter(Boolean)
    .join(", ");
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
