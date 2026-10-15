/**
 * Applying a compared route to a day of the cached itinerary.
 *
 * Kept out of the component so it can be tested without rendering a page: the
 * optimistic order, the rollback and the Undo are the parts most likely to go
 * quietly wrong.
 */

import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, api } from "@/lib/api";
import type { Activity, Itinerary, OptimizerAlgorithm } from "@/types/api";

/**
 * Apply a route to one day of the cached itinerary, optimistically.
 *
 * A plain function rather than a hook so the Undo action can call it by name
 * (a `useCallback` that refers to itself is read before it is declared).
 * `isUndo` stops an undo from offering to undo itself, and makes a 409 speak
 * up — on the first apply RouteLab explains it, but an undo has no dialog.
 */
export async function applyRouteToDay(
  queryClient: QueryClient,
  tripId: string | undefined,
  dayNumber: number,
  placeIds: string[],
  algorithm: OptimizerAlgorithm,
  isUndo = false,
): Promise<Itinerary> {
  const key = ["trip", tripId, "itinerary"];
  const current = queryClient.getQueryData<Itinerary>(key);
  if (!current) throw new Error("The itinerary is still loading.");

  const previous = current.days
    .find((day) => day.day_number === dayNumber)
    ?.activities.filter((activity) => activity.place_id)
    .map((activity) => activity.place_id as string);

  // Show the new order at once. Times and totals still read as they were
  // until the server answers; it owns those, and guessing them here is how
  // a plan ends up disagreeing with itself.
  queryClient.setQueryData<Itinerary>(key, {
    ...current,
    days: current.days.map((day) =>
      day.day_number === dayNumber
        ? { ...day, activities: reorderByPlaceId(day.activities, placeIds) }
        : day,
    ),
  });

  try {
    const updated = await api.itineraries.applyRoute(current.id, dayNumber, {
      place_ids: placeIds,
      algorithm,
      expected_updated_at: current.updated_at,
    });
    queryClient.setQueryData(key, updated);

    if (isUndo) {
      toast.success(`Day ${dayNumber} put back.`);
    } else {
      toast.success(`Day ${dayNumber} reordered.`, {
        action:
          previous && previous.length === placeIds.length
            ? {
                label: "Undo",
                onClick: () => {
                  void applyRouteToDay(
                    queryClient,
                    tripId,
                    dayNumber,
                    previous,
                    algorithm,
                    true,
                  ).catch(() => {
                    // Already reported and rolled back.
                  });
                },
              }
            : undefined,
      });
    }
    return updated;
  } catch (error) {
    // Put the old order back; the optimistic edit was never real.
    queryClient.setQueryData(key, current);
    const stale = error instanceof ApiError && error.status === 409;
    if (!stale || isUndo) {
      toast.error(
        stale
          ? "This day changed since then, so it can't be put back automatically."
          : error instanceof ApiError
            ? error.message
            : "Could not apply that route.",
      );
    }
    throw error;
  }
}

/**
 * Put a day's activities into the given order of place ids.
 *
 * Mirrors what the server does, deliberately: activities with no `place_id` — a
 * block of free time — hold their position in the sequence, and the places are
 * dealt into the slots that remain. If the two disagreed, the optimistic order
 * on screen would differ from the order that came back, and the day would
 * appear to shuffle twice.
 *
 * Any id that is not on the day is ignored and the original order is returned;
 * the server is the one that rejects such a payload, and showing a half-applied
 * order in the meantime would be worse than showing none.
 */
export function reorderByPlaceId(activities: Activity[], placeIds: string[]): Activity[] {
  const byPlaceId = new Map(
    activities
      .filter((activity) => activity.place_id)
      .map((activity) => [activity.place_id as string, activity]),
  );

  const requested = placeIds.map((id) => byPlaceId.get(id));
  if (requested.some((activity) => activity === undefined)) return activities;
  if (requested.length !== byPlaceId.size) return activities;

  const queue = requested as Activity[];
  let next = 0;
  return activities.map((activity) =>
    activity.place_id ? queue[next++] : activity,
  );
}
