import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type {
  Activity,
  AlgorithmRun,
  CompareRouteResponse,
  ItineraryDay,
  OptimizedStop,
} from "@/types/api";

/**
 * The map is Leaflet, which needs a real layout engine. Standing in a marker
 * for it keeps these tests about the dialog — what it requests, what it shows,
 * and what it does on close — rather than about tile rendering.
 */
const mapSpy = vi.hoisted(() => vi.fn());
vi.mock("@/components/map/itinerary-map", () => ({
  ItineraryMap: (props: { stops: unknown[]; baseline?: unknown[] }) => {
    mapSpy(props);
    return (
      <div data-testid="map" data-stops={props.stops.length} data-baseline={props.baseline?.length ?? 0} />
    );
  },
}));

const compareAlgorithms = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, itineraries: { ...actual.api.itineraries, compareAlgorithms } },
  };
});

// Imported after the mocks so the component picks up the stubs.
const { RouteLab } = await import("@/components/trip/route-lab");

function activity(index: number, overrides: Partial<Activity> = {}): Activity {
  return {
    place_id: `place-${index}`,
    title: `Stop ${index}`,
    kind: "visit",
    description: "",
    start_time: `0${8 + index}:00`,
    end_time: `0${9 + index}:00`,
    duration_minutes: 60,
    location: { type: "Point", coordinates: [2.3 + index / 100, 48.8 + index / 100] },
    address: null,
    estimated_cost: 0,
    currency: "EUR",
    rationale: "",
    booking_required: false,
    booking_url: null,
    travel_from_previous: null,
    order: index,
    ...overrides,
  };
}

function day(activityCount: number): ItineraryDay {
  const activities = Array.from({ length: activityCount }, (_, i) => activity(i + 1));
  return {
    day_number: 2,
    day_date: "2026-05-04",
    title: "Day two",
    summary: "",
    activities,
    total_distance_km: 7.5,
    total_walking_km: 7.5,
    estimated_cost: 0,
    activity_count: activities.length,
  };
}

function stop(index: number): OptimizedStop {
  return {
    place_id: `place-${index}`,
    name: `Stop ${index}`,
    day_number: 1,
    order: index,
    arrival_time: "10:00",
    // 90 minutes at each stop, so "Visits" is a figure the test can assert.
    departure_time: "11:30",
    travel_km: 1,
    travel_minutes: 12,
    mode: "walking",
  };
}

function run(overrides: Partial<AlgorithmRun> = {}): AlgorithmRun {
  return {
    algorithm: "ortools",
    label: "OR-Tools",
    solver_status: "OPTIMAL",
    scheduled_count: 3,
    dropped_count: 0,
    total_distance_km: 4.2,
    travel_minutes: 36,
    objective: 300,
    gap_percent: 0,
    duration_seconds: 1.23,
    is_best: true,
    stops: [stop(1), stop(2), stop(3)],
    dropped_place_ids: [],
    ...overrides,
  };
}

const response: CompareRouteResponse = {
  runs: [
    run(),
    run({
      algorithm: "genetic",
      label: "Genetic",
      objective: 330,
      gap_percent: 10,
      is_best: false,
      // Wins on distance and travel time without winning overall, which is
      // what the extra badges exist to show.
      total_distance_km: 3.1,
      travel_minutes: 24,
      duration_seconds: 0.4,
    }),
    run({
      algorithm: "nearest_neighbor",
      label: "Nearest neighbour",
      is_best: false,
      scheduled_count: 0,
      objective: 0,
      stops: [],
      total_distance_km: 0,
      travel_minutes: 0,
    }),
  ],
  best_algorithm: "ortools",
  fastest_algorithm: "genetic",
};

function renderLab(ui: ReactNode) {
  // Retries off: an error state should appear on the first rejection, not after
  // three, and the test should not have to wait out backoff to see it.
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const openDialog = async () => {
  await userEvent.click(screen.getByRole("button", { name: /compare route/i }));
};

beforeEach(() => {
  compareAlgorithms.mockReset();
  mapSpy.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("RouteLab", () => {
  it("renders nothing when no stop has a location to route between", () => {
    const empty = day(0);
    const { container } = renderLab(<RouteLab day={empty} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("ignores activities without a place or a location", async () => {
    const mixed = day(3);
    mixed.activities.push(
      activity(9, { place_id: null, title: "Free time", location: null }),
    );
    compareAlgorithms.mockResolvedValue(response);

    renderLab(<RouteLab day={mixed} />);
    await openDialog();

    await waitFor(() => expect(compareAlgorithms).toHaveBeenCalled());
    const [payload] = compareAlgorithms.mock.calls[0];
    expect(payload.place_ids).toEqual(["place-1", "place-2", "place-3"]);
  });

  it("explains that comparing is not meaningful below three stops, and asks for nothing", async () => {
    renderLab(<RouteLab day={day(2)} />);
    await openDialog();

    expect(await screen.findByText(/not enough stops to compare/i)).toBeInTheDocument();
    expect(screen.getByText(/two routable stops/i)).toBeInTheDocument();
    // The point of the state: no pointless round trip.
    expect(compareAlgorithms).not.toHaveBeenCalled();
  });

  it("shows a loading state while the comparison is in flight", async () => {
    compareAlgorithms.mockReturnValue(new Promise(() => {}));

    renderLab(<RouteLab day={day(4)} />);
    await openDialog();

    expect(await screen.findByText(/solving the same day/i)).toBeInTheDocument();
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });

  it("shows the server's message on failure and retries on request", async () => {
    compareAlgorithms.mockRejectedValueOnce(
      new ApiError(503, "unavailable", "The solver is busy.", {}),
    );

    renderLab(<RouteLab day={day(4)} />);
    await openDialog();

    expect(await screen.findByText("The solver is busy.")).toBeInTheDocument();

    compareAlgorithms.mockResolvedValueOnce(response);
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));

    expect(await screen.findByRole("group")).toBeInTheDocument();
    expect(compareAlgorithms).toHaveBeenCalledTimes(2);
  });

  it("lists every run with its metrics and marks the winners", async () => {
    compareAlgorithms.mockResolvedValue(response);

    renderLab(<RouteLab day={day(4)} />);
    await openDialog();

    const rows = await screen.findAllByRole("button", { name: /score|no feasible/i });
    expect(rows).toHaveLength(3);

    // The best-scoring run carries the overall badge; the run that is merely
    // shorter carries the distance and travel badges instead.
    const best = screen.getByRole("button", { name: /^OR-Tools: score 300 minutes/ });
    expect(within(best).getByText("Best overall")).toBeInTheDocument();
    expect(within(best).queryByText("Shortest")).not.toBeInTheDocument();

    const second = screen.getByRole("button", { name: /^Genetic: score 330 minutes/ });
    expect(within(second).getByText("Shortest")).toBeInTheDocument();
    expect(within(second).getByText("Least travel")).toBeInTheDocument();

    // Travel comes from travel_minutes; visits from each stop's own hours
    // (3 stops x 90 min = 4h 30m); stops from scheduled_count.
    expect(within(best).getByText("36 min")).toBeInTheDocument();
    expect(within(best).getByText("4h 30m")).toBeInTheDocument();
    expect(within(best).getByText("3")).toBeInTheDocument();

    // An infeasible run is listed but cannot be selected.
    const infeasible = screen.getByRole("button", { name: /no feasible plan/i });
    expect(infeasible).toBeDisabled();
  });

  it("previews the selected run against the saved order as a baseline", async () => {
    compareAlgorithms.mockResolvedValue(response);

    renderLab(<RouteLab day={day(4)} />);
    await openDialog();

    const map = await screen.findByTestId("map");
    // Three stops in the plan, four saved activities underneath it.
    expect(map).toHaveAttribute("data-stops", "3");
    expect(map).toHaveAttribute("data-baseline", "4");

    await userEvent.click(
      screen.getByRole("button", { name: /^Genetic: score 330 minutes/ }),
    );
    expect(
      screen.getByRole("button", { name: /^Genetic: score 330 minutes/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("aborts the request and keeps no results when closed mid-flight", async () => {
    let signal: AbortSignal | undefined;
    compareAlgorithms.mockImplementation(
      (_payload: unknown, incoming: AbortSignal) => {
        signal = incoming;
        return new Promise((_resolve, reject) => {
          incoming.addEventListener("abort", () => reject(incoming.reason));
        });
      },
    );

    renderLab(<RouteLab day={day(4)} />);
    await openDialog();
    expect(await screen.findByText(/solving the same day/i)).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(signal?.aborted).toBe(true));
    // Nothing survives the close: reopening starts over rather than showing a
    // stale table or the abort as an error.
    expect(screen.queryByText(/could not compare/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });

  it("reports a timeout honestly rather than spinning forever", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    compareAlgorithms.mockImplementation(
      (_payload: unknown, incoming: AbortSignal) =>
        new Promise((_resolve, reject) => {
          incoming.addEventListener("abort", () => reject(incoming.reason));
        }),
    );

    renderLab(<RouteLab day={day(4)} />);
    await openDialog();
    expect(await screen.findByText(/solving the same day/i)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(20_000);

    expect(await screen.findByText(/took longer than 20 seconds/i)).toBeInTheDocument();
  });

  describe("applying a run", () => {
    it("offers nothing to apply on a read-only itinerary", async () => {
      compareAlgorithms.mockResolvedValue(response);
      renderLab(<RouteLab day={day(4)} />);
      await openDialog();

      await screen.findByRole("group");
      expect(screen.queryByRole("button", { name: /use this route/i })).not.toBeInTheDocument();
    });

    it("hands the selected run to onApply and closes on success", async () => {
      compareAlgorithms.mockResolvedValue(response);
      const onApply = vi.fn().mockResolvedValue(undefined);
      renderLab(<RouteLab day={day(4)} onApply={onApply} />);
      await openDialog();

      await userEvent.click(
        await screen.findByRole("button", { name: /^Genetic: score 330 minutes/ }),
      );
      await userEvent.click(screen.getByRole("button", { name: /use this route/i }));

      expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ algorithm: "genetic" }));
      await waitFor(() => expect(screen.queryByRole("group")).not.toBeInTheDocument());
    });

    it("explains instead of applying a plan that drops stops", async () => {
      compareAlgorithms.mockResolvedValue({
        ...response,
        runs: [run({ dropped_count: 1, dropped_place_ids: ["place-4"] })],
      });
      renderLab(<RouteLab day={day(4)} onApply={vi.fn()} />);
      await openDialog();

      expect(await screen.findByText(/leaves out 1 stop,/i)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /use this route/i })).not.toBeInTheDocument();
    });

    it("on 409, says the day changed and compares again on request", async () => {
      compareAlgorithms.mockResolvedValue(response);
      const onApply = vi
        .fn()
        .mockRejectedValue(new ApiError(409, "conflict", "The itinerary changed.", {}));
      renderLab(<RouteLab day={day(4)} onApply={onApply} />);
      await openDialog();

      await userEvent.click(await screen.findByRole("button", { name: /use this route/i }));

      expect(
        await screen.findByText(/this day changed while you were comparing/i),
      ).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /compare again/i }));

      await waitFor(() => expect(compareAlgorithms).toHaveBeenCalledTimes(2));
      expect(screen.queryByText(/this day changed/i)).not.toBeInTheDocument();
    });

    it("shows any other failure without offering to compare again", async () => {
      compareAlgorithms.mockResolvedValue(response);
      const onApply = vi
        .fn()
        .mockRejectedValue(new ApiError(422, "validation_error", "Those places are not on this day.", {}));
      renderLab(<RouteLab day={day(4)} onApply={onApply} />);
      await openDialog();

      await userEvent.click(await screen.findByRole("button", { name: /use this route/i }));

      expect(await screen.findByText(/could not apply that route/i)).toBeInTheDocument();
      expect(screen.getByText("Those places are not on this day.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /compare again/i })).not.toBeInTheDocument();
    });
  });
});
