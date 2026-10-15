import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { Activity, Itinerary } from "@/types/api";

const applyRoute = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, itineraries: { ...actual.api.itineraries, applyRoute } },
  };
});

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const { applyRouteToDay, reorderByPlaceId } = await import("@/lib/apply-route");

const KEY = ["trip", "trip-1", "itinerary"];

function activity(placeId: string | null, title = placeId ?? "Free time"): Activity {
  return {
    place_id: placeId,
    title,
    kind: placeId ? "visit" : "free_time",
    description: "",
    start_time: "10:00",
    end_time: "11:00",
    duration_minutes: 60,
    location: null,
    address: null,
    estimated_cost: 0,
    currency: "EUR",
    rationale: "",
    booking_required: false,
    booking_url: null,
    travel_from_previous: null,
    order: 0,
  };
}

function itinerary(placeIds: (string | null)[], updatedAt = "2026-05-01T10:00:00.123Z"): Itinerary {
  return {
    id: "itin-1",
    trip_id: "trip-1",
    days: [
      {
        day_number: 1,
        day_date: "2026-05-04",
        title: "",
        summary: "",
        activities: placeIds.map((id) => activity(id)),
        total_distance_km: 0,
        total_walking_km: 0,
        estimated_cost: 0,
        activity_count: placeIds.length,
      },
    ],
    updated_at: updatedAt,
  } as Itinerary;
}

const order = (data: Itinerary | undefined) =>
  data?.days[0].activities.map((a) => a.place_id);

let client: QueryClient;

beforeEach(() => {
  applyRoute.mockReset();
  toast.success.mockReset();
  toast.error.mockReset();
  client = new QueryClient();
});

describe("reorderByPlaceId", () => {
  it("deals places into the routable slots and leaves free time where it was", () => {
    const activities = itinerary(["a", null, "b", "c"]).days[0].activities;
    const result = reorderByPlaceId(activities, ["c", "a", "b"]);
    expect(result.map((a) => a.place_id)).toEqual(["c", null, "a", "b"]);
  });

  it("leaves the order alone when an id is not on the day", () => {
    const activities = itinerary(["a", "b", "c"]).days[0].activities;
    expect(reorderByPlaceId(activities, ["a", "b", "zzz"])).toBe(activities);
  });

  it("leaves the order alone when a place is missing", () => {
    const activities = itinerary(["a", "b", "c"]).days[0].activities;
    expect(reorderByPlaceId(activities, ["a", "b"])).toBe(activities);
  });
});

describe("applyRouteToDay", () => {
  it("shows the new order at once, sends the token, then keeps the server's copy", async () => {
    client.setQueryData(KEY, itinerary(["a", "b", "c"]));
    const fromServer = itinerary(["c", "a", "b"], "2026-05-01T10:00:05.000Z");

    let resolve!: (value: Itinerary) => void;
    applyRoute.mockReturnValue(new Promise((r) => (resolve = r)));

    const pending = applyRouteToDay(client, "trip-1", 1, ["c", "a", "b"], "genetic");

    // Optimistic: reordered before the server has answered.
    expect(order(client.getQueryData(KEY))).toEqual(["c", "a", "b"]);
    expect(applyRoute).toHaveBeenCalledWith("itin-1", 1, {
      place_ids: ["c", "a", "b"],
      algorithm: "genetic",
      expected_updated_at: "2026-05-01T10:00:00.123Z",
    });

    resolve(fromServer);
    await pending;
    expect(client.getQueryData(KEY)).toEqual(fromServer);
    expect(toast.success).toHaveBeenCalledWith(
      "Day 1 reordered.",
      expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }),
    );
  });

  it("rolls back and reports when the server refuses", async () => {
    const before = itinerary(["a", "b", "c"]);
    client.setQueryData(KEY, before);
    applyRoute.mockRejectedValue(new ApiError(422, "validation_error", "Nope.", {}));

    await expect(
      applyRouteToDay(client, "trip-1", 1, ["c", "a", "b"], "genetic"),
    ).rejects.toBeInstanceOf(ApiError);

    expect(client.getQueryData(KEY)).toEqual(before);
    expect(toast.error).toHaveBeenCalledWith("Nope.");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("rolls back quietly on 409, leaving the explanation to the dialog", async () => {
    const before = itinerary(["a", "b", "c"]);
    client.setQueryData(KEY, before);
    applyRoute.mockRejectedValue(new ApiError(409, "conflict", "Stale.", {}));

    await expect(
      applyRouteToDay(client, "trip-1", 1, ["c", "a", "b"], "genetic"),
    ).rejects.toBeInstanceOf(ApiError);

    expect(client.getQueryData(KEY)).toEqual(before);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("undoes by applying the previous order, with the new token, and offers no second undo", async () => {
    client.setQueryData(KEY, itinerary(["a", "b", "c"]));
    const applied = itinerary(["c", "a", "b"], "2026-05-01T10:00:05.000Z");
    const restored = itinerary(["a", "b", "c"], "2026-05-01T10:00:09.000Z");
    applyRoute.mockResolvedValueOnce(applied).mockResolvedValueOnce(restored);

    await applyRouteToDay(client, "trip-1", 1, ["c", "a", "b"], "genetic");
    const [, options] = toast.success.mock.calls[0];
    options.action.onClick();

    await vi.waitFor(() => expect(client.getQueryData(KEY)).toEqual(restored));
    expect(applyRoute).toHaveBeenLastCalledWith("itin-1", 1, {
      place_ids: ["a", "b", "c"],
      algorithm: "genetic",
      // The token from the apply, not the stale one from before it.
      expected_updated_at: "2026-05-01T10:00:05.000Z",
    });
    expect(toast.success).toHaveBeenLastCalledWith("Day 1 put back.");
  });

  it("says so when an undo arrives after the day has changed again", async () => {
    client.setQueryData(KEY, itinerary(["a", "b", "c"]));
    applyRoute
      .mockResolvedValueOnce(itinerary(["c", "a", "b"], "2026-05-01T10:00:05.000Z"))
      .mockRejectedValueOnce(new ApiError(409, "conflict", "Stale.", {}));

    await applyRouteToDay(client, "trip-1", 1, ["c", "a", "b"], "genetic");
    toast.success.mock.calls[0][1].action.onClick();

    await vi.waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringMatching(/can't be put back automatically/),
      ),
    );
    expect(order(client.getQueryData(KEY))).toEqual(["c", "a", "b"]);
  });
});
