"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarDays,
  Check,
  Copy,
  Footprints,
  Link2,
  RefreshCw,
  Route,
  Share2,
  Trash2,
  Users,
  Wallet,
} from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { ItineraryMap, activitiesToStops } from "@/components/map/itinerary-map";
import {
  DaySelector,
  DayTimeline,
} from "@/components/trip/itinerary-timeline";
import { AlgorithmSelect } from "@/components/trip/algorithm-select";
import { RouteLab } from "@/components/trip/route-lab";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/overlays";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { ApiError, api } from "@/lib/api";
import { applyRouteToDay } from "@/lib/apply-route";
import { blurPhoto, cityImage, photo } from "@/lib/images";
import {
  formatDateRange,
  formatDistance,
  formatMoney,
  pluralize,
} from "@/lib/utils";
import type { Itinerary, OptimizerAlgorithm } from "@/types/api";

export function ItineraryView({
  tripId,
  readOnly = false,
  sharedItinerary,
}: {
  tripId?: string;
  readOnly?: boolean;
  /** Supplied on the public share page, where there is no trip to fetch. */
  sharedItinerary?: Itinerary;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [activeDay, setActiveDay] = useState(1);
  const [shareOpen, setShareOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const tripQuery = useQuery({
    queryKey: ["trip", tripId],
    queryFn: () => api.trips.get(tripId!),
    enabled: Boolean(tripId) && !sharedItinerary,
  });

  const itineraryQuery = useQuery({
    queryKey: ["trip", tripId, "itinerary"],
    queryFn: () => api.trips.itinerary(tripId!),
    enabled: Boolean(tripId) && !sharedItinerary,
  });

  const itinerary = sharedItinerary ?? itineraryQuery.data;
  const trip = tripQuery.data;

  // Clamp the selected day when a regenerate returns a shorter itinerary.
  // Derived rather than corrected in an effect: there is no frame on which the
  // view points at a day the itinerary no longer has.
  const dayCount = itinerary?.days.length ?? 0;
  const selectedDay = dayCount > 0 && activeDay > dayCount ? 1 : activeDay;

  const regenerate = useMutation({
    mutationFn: () => api.trips.generate(tripId!, { regenerate: true }),
    onSuccess() {
      toast.success("A fresh plan is ready.");
      queryClient.invalidateQueries({ queryKey: ["trip", tripId] });
    },
    onError(error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not rebuild the plan.",
      );
    },
  });

  // Saving the algorithm and re-planning are one action from the traveller's
  // point of view: choosing a different solver means nothing until a plan comes
  // back from it. Persisting first means a failed re-plan still leaves the
  // choice saved, so retrying does not require picking it again.
  const setAlgorithm = useMutation({
    async mutationFn(algorithm: OptimizerAlgorithm) {
      if (!trip) throw new Error("The trip is still loading.");
      await api.trips.update(trip.id, {
        constraints: { ...trip.constraints, optimizer_algorithm: algorithm },
      });
      return api.trips.generate(trip.id, { regenerate: true });
    },
    onSuccess() {
      toast.success("Re-planned with the new algorithm.");
      queryClient.invalidateQueries({ queryKey: ["trip", tripId] });
    },
    onError(error) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not re-plan with that algorithm.",
      );
    },
  });

  /**
   * Apply a compared route to a day, then offer to put it back.
   *
   * Undo is held client-side rather than as server history: the order being
   * replaced is already on screen, so keeping it costs nothing, whereas a
   * one-step history on the server would mean a new field on every itinerary
   * and a second endpoint for an affordance that lives as long as a toast. The
   * undo goes back through apply-route, so restoring an order is revalidated
   * exactly like choosing one.
   */
  const applyRoute = useCallback(
    (dayNumber: number, placeIds: string[], algorithm: OptimizerAlgorithm) =>
      applyRouteToDay(queryClient, tripId, dayNumber, placeIds, algorithm),
    [queryClient, tripId],
  );

  const removeActivity = useMutation({
    mutationFn: ({ day, index }: { day: number; index: number }) =>
      api.itineraries.removeActivity(itinerary!.id, day, index),
    onSuccess(updated) {
      // Write straight into the cache: the server already returned the
      // recomputed itinerary, so refetching would be a wasted round trip.
      queryClient.setQueryData(["trip", tripId, "itinerary"], updated);
      toast.success("Removed. The rest of the day has been re-timed.");
    },
    onError(error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not remove that.",
      );
    },
  });

  const share = useMutation({
    mutationFn: () => api.trips.share(tripId!),
    onSuccess(updated) {
      queryClient.setQueryData(["trip", tripId], updated);
      setShareOpen(true);
    },
    onError: () => toast.error("Could not create a share link."),
  });

  const unshare = useMutation({
    mutationFn: () => api.trips.unshare(tripId!),
    onSuccess(updated) {
      queryClient.setQueryData(["trip", tripId], updated);
      toast.success("Share link turned off.");
      setShareOpen(false);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.trips.remove(tripId!),
    onSuccess() {
      toast.success("Trip deleted.");
      router.push("/trips");
    },
    onError: () => toast.error("Could not delete the trip."),
  });

  const currentDay = useMemo(
    () => itinerary?.days.find((d) => d.day_number === selectedDay),
    [itinerary, selectedDay],
  );

  if (itineraryQuery.isLoading || tripQuery.isLoading) {
    return <ItinerarySkeleton />;
  }

  if (itineraryQuery.isError || !itinerary) {
    const error = itineraryQuery.error;
    const notPlanned = error instanceof ApiError && error.status === 404;
    return (
      <div className="mx-auto max-w-2xl px-4 py-24 text-center">
        <h1 className="text-2xl font-bold">
          {notPlanned ? "This trip has no itinerary yet" : "Could not load this trip"}
        </h1>
        <p className="mt-2 text-muted-foreground">
          {notPlanned
            ? "Build one and it will appear here."
            : error instanceof ApiError
              ? error.message
              : "Something went wrong."}
        </p>
        {notPlanned && tripId && (
          <Button
            className="mt-6"
            size="lg"
            onClick={() => regenerate.mutate()}
            isLoading={regenerate.isPending}
            loadingText="Building…"
          >
            Build the itinerary
          </Button>
        )}
      </div>
    );
  }

  const city = trip?.destination.city ?? "";
  const imageKey = cityImage(city || "traveller");
  const report = itinerary.optimization;
  const saved = report.distance_saved_km;

  return (
    <>
      {/* Hero */}
      <section className="relative isolate">
        <div className="absolute inset-0 -z-10">
          <Image
            src={photo(imageKey, { width: 1900, height: 700 })}
            alt=""
            fill
            priority
            sizes="100vw"
            placeholder="blur"
            blurDataURL={blurPhoto(imageKey)}
            className="object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-slate-950/92 via-slate-950/55 to-slate-950/45" />
        </div>

        <div className="mx-auto max-w-7xl px-4 pb-10 pt-12 sm:px-6 lg:px-8 lg:pb-14 lg:pt-16">
          <Badge variant="glass" size="lg">
            <Route />
            Version {itinerary.version}
          </Badge>

          <h1 className="mt-4 max-w-3xl text-3xl font-extrabold text-white sm:text-4xl lg:text-5xl">
            {itinerary.title || `Your trip to ${city}`}
          </h1>

          {trip && (
            <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-white/85">
              <span className="flex items-center gap-1.5">
                <CalendarDays className="size-4" />
                {formatDateRange(trip.start_date, trip.end_date)}
              </span>
              <span className="flex items-center gap-1.5">
                <Users className="size-4" />
                {trip.party.adults + trip.party.children + trip.party.infants}{" "}
                {pluralize(
                  trip.party.adults + trip.party.children + trip.party.infants,
                  "traveller",
                )}
              </span>
            </p>
          )}

          {itinerary.summary && (
            <p className="mt-5 max-w-2xl leading-relaxed text-white/80">
              {itinerary.summary}
            </p>
          )}

          {/* Headline numbers */}
          <dl className="mt-8 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile
              icon={<CalendarDays />}
              label="Days"
              value={String(itinerary.days.length)}
            />
            <StatTile
              icon={<Route />}
              label="Stops"
              value={String(report.places_scheduled)}
            />
            <StatTile
              icon={<Footprints />}
              label="Distance"
              value={formatDistance(itinerary.total_distance_km)}
            />
            <StatTile
              icon={<Wallet />}
              label="Estimated"
              value={formatMoney(itinerary.total_estimated_cost, itinerary.currency)}
            />
          </dl>

          {!readOnly && tripId && (
            <div className="mt-8 flex flex-wrap gap-3">
              <Button
                variant="glass"
                onClick={() => regenerate.mutate()}
                isLoading={regenerate.isPending}
                loadingText="Rebuilding…"
              >
                <RefreshCw />
                Rebuild plan
              </Button>
              <Button
                variant="glass"
                onClick={() =>
                  trip?.share_token ? setShareOpen(true) : share.mutate()
                }
                isLoading={share.isPending}
              >
                <Share2 />
                Share
              </Button>
              <Button variant="glass" onClick={() => setDeleteOpen(true)}>
                <Trash2 />
                Delete
              </Button>
            </div>
          )}
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        {/* What the solver did — shown, not hidden. */}
        {report.ran && saved != null && saved > 0 && (
          <Card className="mb-8 border-primary/25 bg-primary-soft p-5">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-primary">
              <Check className="size-4 shrink-0" strokeWidth={3} />
              <span className="font-semibold">
                Route optimised: {formatDistance(saved)} less travel
              </span>
              <span className="opacity-80">
                ({formatDistance(report.distance_before_km ?? 0)} →{" "}
                {formatDistance(report.distance_after_km ?? 0)}) in{" "}
                {report.duration_seconds.toFixed(1)}s
              </span>
            </p>
          </Card>
        )}

        {itinerary.generation.warnings.length > 0 && (
          <Card className="mb-8 border-warning/30 bg-warning/10 p-5">
            <p className="flex items-center gap-2 font-semibold text-warning-foreground dark:text-warning">
              <AlertTriangle className="size-4" />
              Worth knowing
            </p>
            <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-muted-foreground">
              {itinerary.generation.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Card>
        )}

        <DaySelector
          days={itinerary.days}
          active={selectedDay}
          onSelect={setActiveDay}
        />

        <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_400px]">
          <div>
            {currentDay && (
              <DayTimeline
                day={currentDay}
                currency={itinerary.currency}
                readOnly={readOnly}
                onRemoveActivity={
                  readOnly
                    ? undefined
                    : (day, index) => removeActivity.mutate({ day, index })
                }
              />
            )}
          </div>

          {/* The map tracks the selected day and stays put while the timeline
              scrolls, so the two never drift out of sync. */}
          <aside className="lg:sticky lg:top-24 lg:flex lg:h-[calc(100svh-8rem)] lg:flex-col lg:gap-3">
            <div className="h-[420px] lg:min-h-[320px] lg:flex-1">
              {currentDay && (
                <ItineraryMap
                  stops={activitiesToStops(currentDay.activities)}
                  distanceKm={currentDay.total_distance_km}
                  className="h-full"
                />
              )}
            </div>
            {trip && (
              <div className="mt-3 lg:mt-0 lg:shrink-0">
                <AlgorithmSelect
                  id="day-algorithm"
                  label="Route algorithm"
                  value={trip.constraints.optimizer_algorithm}
                  onChange={(algorithm) => setAlgorithm.mutate(algorithm)}
                  disabled={setAlgorithm.isPending || regenerate.isPending}
                />
              </div>
            )}
            {currentDay && (
              <RouteLab
                day={currentDay}
                transportMode={trip?.constraints.preferred_transport}
                className="mt-3 lg:mt-0 lg:shrink-0"
                onApply={async (run) => {
                  await applyRoute(
                    currentDay.day_number,
                    run.stops.map((stop) => stop.place_id),
                    run.algorithm as OptimizerAlgorithm,
                  );
                }}
              />
            )}
          </aside>
        </div>
      </main>

      {/* Share dialog */}
      <Dialog open={shareOpen} onOpenChange={setShareOpen}>
        <DialogContent sheetOnMobile>
          <DialogHeader>
            <DialogTitle>Share this itinerary</DialogTitle>
            <DialogDescription>
              Anyone with the link can view the plan. They cannot change it, and
              they do not need an account.
            </DialogDescription>
          </DialogHeader>
          {trip?.share_token && <ShareLink token={trip.share_token} />}
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => unshare.mutate()}
              isLoading={unshare.isPending}
            >
              Turn off sharing
            </Button>
            <Button onClick={() => setShareOpen(false)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent sheetOnMobile>
          <DialogHeader>
            <DialogTitle>Delete this trip?</DialogTitle>
            <DialogDescription>
              Every version of the itinerary goes with it. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Keep it
            </Button>
            <Button
              variant="destructive"
              onClick={() => remove.mutate()}
              isLoading={remove.isPending}
              loadingText="Deleting…"
            >
              Delete trip
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function StatTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="glass-dark rounded-2xl px-4 py-3.5">
      <dt className="flex items-center gap-1.5 text-xs font-medium text-white/70 [&_svg]:size-3.5">
        {icon}
        {label}
      </dt>
      <dd className="mt-1 text-xl font-bold text-white">{value}</dd>
    </div>
  );
}

function ShareLink({ token }: { token: string }) {
  const [copied, setCopied] = useState(false);
  const url =
    typeof window !== "undefined"
      ? `${window.location.origin}/shared/${token}`
      : `/shared/${token}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be blocked; the input is selectable as a fallback.
      toast.error("Could not copy — select the link and copy it manually.");
    }
  }

  return (
    <div className="flex items-center gap-2 rounded-xl border border-border bg-secondary/50 p-2">
      <Link2 className="ml-2 size-4 shrink-0 text-muted-foreground" />
      <input
        readOnly
        value={url}
        onFocus={(event) => event.currentTarget.select()}
        className="min-w-0 flex-1 bg-transparent text-sm outline-none"
        aria-label="Share link"
      />
      <Button size="sm" variant={copied ? "secondary" : "primary"} onClick={copy}>
        {copied ? <Check /> : <Copy />}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

function ItinerarySkeleton() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <Skeleton className="h-10 w-2/3 max-w-lg" />
      <Skeleton className="mt-4 h-5 w-64" />
      <div className="mt-8 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-20 rounded-2xl" />
        ))}
      </div>
      <div className="mt-10 flex gap-2.5">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-20 w-28 rounded-2xl" />
        ))}
      </div>
      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_400px]">
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28 rounded-2xl" />
          ))}
        </div>
        <Skeleton className="h-96 rounded-2xl" />
      </div>
    </div>
  );
}
