"""Route optimisation algorithms.

The OR-Tools model in ``engine.py`` is one way to plan a multi-day itinerary. This
module adds five more, so the trade-off between speed and plan quality is
something a caller can choose and measure rather than take on trust:

=======================  ===============================================================
``nearest_neighbor``     Greedy: from where you are, go to the closest place that still
                         fits. Fastest, and usually the weakest.
``cheapest_insertion``   Builds the whole plan by repeatedly inserting the place with the
                         best net gain at its cheapest position, across all days at once.
``local_search``         Cheapest insertion, then improved by 2-opt, Or-opt, relocate,
                         swap and replace moves until no move helps (a local optimum).
``simulated_annealing``  Local search that sometimes accepts a worse plan, with a falling
                         "temperature", so it can escape local optima.
``genetic``              Evolves a population of visiting orders with order crossover,
                         mutation and elitism.
=======================  ===============================================================

Every algorithm here, and the OR-Tools solver once its answer is converted, is
scored by **one** evaluator (:meth:`RoutingProblem.objective`). That evaluator
reproduces the OR-Tools model exactly, so objective values are directly comparable:

* cost of a day = travel minutes + visit minutes, including the trip back to the base;
* a place left out costs ``600 + 1200 x score`` minutes (mandatory: ten million);
* a day must respect opening hours, the daily time window, the cap on places per day
  and the cap on distance, and may wait at most ``max_wait_minutes`` for a place to
  open (the first stop of a day can start later instead, as in the OR-Tools model).

Lower objective is better. The randomised algorithms are seeded from the request, so
the same request always produces the same plan.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from app.core.config import OptimizerAlgorithm
from app.optimization.distance import (
    build_travel_matrix,
    effective_mode,
    travel_minutes,
)
from app.optimization.schemas import (
    DaySchedule,
    OptimizationRequest,
    OptimizationResult,
    ScheduledStop,
)

# Same pricing as the OR-Tools model, so objectives line up.
DROP_PENALTY_BASE = 600
DROP_PENALTY_SCORE_WEIGHT = 1200
MANDATORY_UNSERVED_PENALTY = 10_000_000
# Mandatory places are inserted before anything else when constructing a plan.
_MANDATORY_FIRST = 5_000_000_000

# A move that improves the objective by less than this is treated as no improvement,
# which keeps floating-point noise from looping forever.
_EPSILON = 0.5

# Moves are ``(day -> new route, places newly assigned, places newly unassigned)``.
Move = tuple[dict[int, list[int]], tuple[int, ...], tuple[int, ...]]


@dataclass(slots=True)
class Solution:
    """Which places go on which day, in what order, and which are left out.

    ``unassigned`` holds only places that *could* be scheduled; a place that is
    closed on every day of the trip can never be placed and is not tracked here.
    """

    routes: list[list[int]]
    unassigned: set[int] = field(default_factory=set)

    def copy(self) -> Solution:
        return Solution([r[:] for r in self.routes], set(self.unassigned))


class RoutingProblem:
    """A request, pre-processed once into plain lists so the inner loops stay fast."""

    def __init__(self, request: OptimizationRequest) -> None:
        self.request = request
        self.places = request.places
        self.days = request.days
        self.n = len(request.places)
        self.num_days = len(request.days)

        coords = [request.origin] + [p.coordinate for p in request.places]
        dist, dur = build_travel_matrix(coords, request.mode)
        # Node 0 is the base; place ``i`` is node ``i + 1``. Plain lists beat numpy
        # scalar indexing by a wide margin in tight Python loops.
        self.dist: list[list[float]] = dist.tolist()
        self.dur: list[list[int]] = dur.tolist()
        self.service = [0] + [p.visit_minutes for p in request.places]

        self.cap = request.max_places_per_day
        # Mirrors the OR-Tools distance capacity, which is never below one km.
        self.max_km = max(1000, int(request.max_daily_distance_km * 1000)) / 1000.0
        self.max_wait = request.max_wait_minutes

        # win[d][i] = (earliest start, latest start) in day-local minutes, or None.
        self.win: list[list[tuple[int, int] | None]] = []
        for day in request.days:
            row: list[tuple[int, int] | None] = []
            for place in request.places:
                window = place.window_for(day.weekday)
                if window is None:
                    row.append(None)
                    continue
                earliest = max(day.start_minutes, window[0])
                latest = min(day.end_minutes, window[1]) - place.visit_minutes
                row.append((earliest, latest) if latest >= earliest else None)
            self.win.append(row)

        self.schedulable = [
            any(self.win[d][i] is not None for d in range(self.num_days))
            for i in range(self.n)
        ]
        self.mandatory = [p.mandatory for p in request.places]
        self.penalty = [self._penalty(i) for i in range(self.n)]

        self._cache: dict[tuple[int, tuple[int, ...]], tuple[int, float] | None] = {}

    # ------------------------------------------------------------------ pricing

    def _penalty(self, i: int) -> int:
        if not self.schedulable[i]:
            return 0  # can never be visited, so leaving it out costs nothing
        place = self.places[i]
        if place.mandatory:
            return MANDATORY_UNSERVED_PENALTY
        score = max(0.0, min(1.0, place.score))
        return DROP_PENALTY_BASE + int(DROP_PENALTY_SCORE_WEIGHT * score)

    # --------------------------------------------------------------- evaluation

    def route_eval(self, d: int, route: list[int]) -> tuple[int, float] | None:
        """Cost in minutes and distance in km of one day, or ``None`` if infeasible."""
        key = (d, tuple(route))
        if key in self._cache:
            return self._cache[key]
        result = self._simulate(d, route)
        if len(self._cache) > 400_000:  # bound memory on very long runs
            self._cache.clear()
        self._cache[key] = result
        return result

    def _simulate(self, d: int, route: list[int]) -> tuple[int, float] | None:
        if len(route) > self.cap:
            return None
        day_end = self.days[d].end_minutes
        windows = self.win[d]
        dur, dist, service = self.dur, self.dist, self.service

        clock = self.days[d].start_minutes
        prev = 0
        cost = 0
        km = 0.0
        for position, i in enumerate(route):
            window = windows[i]
            if window is None:
                return None
            node = i + 1
            arrive = clock + service[prev] + dur[prev][node]
            start = arrive if arrive >= window[0] else window[0]
            # Waiting is capped, except before the first stop: a day can simply
            # start later, which is what the OR-Tools model does too.
            if position > 0 and start - arrive > self.max_wait:
                return None
            if start > window[1]:
                return None
            cost += dur[prev][node] + service[prev]
            km += dist[prev][node]
            clock, prev = start, node

        if route:
            if clock + service[prev] + dur[prev][0] > day_end:
                return None
            cost += service[prev] + dur[prev][0]
            km += dist[prev][0]
            if km > self.max_km:
                return None
        return cost, km

    def objective(self, sol: Solution) -> int:
        """Total cost of a plan: day costs plus the price of everything left out."""
        total = 0
        for d, route in enumerate(sol.routes):
            evaluated = self.route_eval(d, route)
            if evaluated is None:
                return 10**15  # an infeasible plan is never better than a real one
            total += evaluated[0]
        return total + sum(self.penalty[i] for i in sol.unassigned)

    def empty_solution(self) -> Solution:
        return Solution(
            [[] for _ in range(self.num_days)],
            {i for i in range(self.n) if self.schedulable[i]},
        )

    # ----------------------------------------------------------- move machinery

    def move_delta(self, sol: Solution, move: Move) -> int | None:
        """Change in objective if ``move`` were applied, or ``None`` if infeasible."""
        changes, added, removed = move
        delta = 0
        for d, new_route in changes.items():
            new = self.route_eval(d, new_route)
            if new is None:
                return None
            old = self.route_eval(d, sol.routes[d])
            delta += new[0] - (old[0] if old else 0)
        for i in added:
            delta -= self.penalty[i]
        for i in removed:
            delta += self.penalty[i]
        return delta

    @staticmethod
    def apply(sol: Solution, move: Move) -> None:
        changes, added, removed = move
        for d, new_route in changes.items():
            sol.routes[d] = new_route
        sol.unassigned.difference_update(added)
        sol.unassigned.update(removed)

    # Each builder returns a Move or None; they are shared by the exhaustive local
    # search and the random moves of simulated annealing.

    @staticmethod
    def mv_two_opt(sol: Solution, d: int, a: int, b: int) -> Move:
        route = sol.routes[d]
        return ({d: route[:a] + route[a : b + 1][::-1] + route[b + 1 :]}, (), ())

    @staticmethod
    def mv_relocate(
        sol: Solution, d1: int, p1: int, length: int, d2: int, p2: int, reverse: bool
    ) -> Move | None:
        """Move ``length`` consecutive stops from (d1, p1) to position p2 of day d2."""
        source = sol.routes[d1]
        segment = source[p1 : p1 + length]
        if len(segment) != length:
            return None
        if reverse:
            segment = segment[::-1]
        remaining = source[:p1] + source[p1 + length :]
        if d1 == d2:
            if p2 > len(remaining):
                return None
            target = remaining[:p2] + segment + remaining[p2:]
            if target == source:
                return None
            return ({d1: target}, (), ())
        destination = sol.routes[d2]
        if p2 > len(destination):
            return None
        return (
            {d1: remaining, d2: destination[:p2] + segment + destination[p2:]},
            (),
            (),
        )

    @staticmethod
    def mv_swap(sol: Solution, d1: int, p1: int, d2: int, p2: int) -> Move | None:
        if d1 == d2:
            if p1 == p2:
                return None
            route = sol.routes[d1][:]
            route[p1], route[p2] = route[p2], route[p1]
            return ({d1: route}, (), ())
        first, second = sol.routes[d1][:], sol.routes[d2][:]
        first[p1], second[p2] = second[p2], first[p1]
        return ({d1: first, d2: second}, (), ())

    @staticmethod
    def mv_insert(sol: Solution, i: int, d: int, p: int) -> Move:
        route = sol.routes[d]
        return ({d: [*route[:p], i, *route[p:]]}, (i,), ())

    @staticmethod
    def mv_replace(sol: Solution, i: int, d: int, p: int) -> Move:
        route = sol.routes[d][:]
        out = route[p]
        route[p] = i
        return ({d: route}, (i,), (out,))

    @staticmethod
    def mv_remove(sol: Solution, d: int, p: int) -> Move:
        route = sol.routes[d]
        return ({d: route[:p] + route[p + 1 :]}, (), (route[p],))

    # ------------------------------------------------------------- construction

    def _best_position(self, d: int, i: int, route: list[int]) -> tuple[int, int] | None:
        """Cheapest feasible position to insert ``i`` into ``route``: (delta, position)."""
        base = self.route_eval(d, route)
        base_cost = base[0] if base else 0
        best: tuple[int, int] | None = None
        for position in range(len(route) + 1):
            evaluated = self.route_eval(d, [*route[:position], i, *route[position:]])
            if evaluated is None:
                continue
            delta = evaluated[0] - base_cost
            if best is None or delta < best[0]:
                best = (delta, position)
        return best

    def nearest_neighbor(self, deadline: float | None = None) -> Solution:
        """Day by day, always walk to the closest place that still fits."""
        sol = self.empty_solution()
        for d in range(self.num_days):
            route: list[int] = []
            while len(route) < self.cap and sol.unassigned:
                if deadline is not None and time.perf_counter() > deadline:
                    break
                last = route[-1] + 1 if route else 0
                best: tuple[tuple[bool, int, float, int], int] | None = None
                for i in sol.unassigned:
                    if self.win[d][i] is None:
                        continue
                    if self.route_eval(d, [*route, i]) is None:
                        continue
                    arc = self.dur[last][i + 1] + self.service[last]
                    key = (not self.mandatory[i], arc, -self.places[i].score, i)
                    if best is None or key < best[0]:
                        best = (key, i)
                if best is None:
                    break
                route.append(best[1])
                sol.unassigned.discard(best[1])
            sol.routes[d] = route
        return sol

    def cheapest_insertion(self, deadline: float | None = None) -> Solution:
        """Insert whichever place gains the most, wherever it is cheapest to add."""
        sol = self.empty_solution()
        # (place, day) -> cheapest (delta, position); only a changed day goes stale.
        cache: dict[tuple[int, int], tuple[int, int] | None] = {}

        while sol.unassigned:
            if deadline is not None and time.perf_counter() > deadline:
                break
            best: tuple[int, int, int, int, int] | None = None  # gain, i, d, pos, delta
            for i in sorted(sol.unassigned):
                choice: tuple[int, int, int] | None = None  # delta, d, pos
                for d in range(self.num_days):
                    if len(sol.routes[d]) >= self.cap or self.win[d][i] is None:
                        continue
                    key = (i, d)
                    if key not in cache:
                        cache[key] = self._best_position(d, i, sol.routes[d])
                    found = cache[key]
                    if found is not None and (choice is None or found[0] < choice[0]):
                        choice = (found[0], d, found[1])
                if choice is None:
                    continue
                delta, d, position = choice
                gain = self.penalty[i] - delta
                if self.mandatory[i]:
                    gain += _MANDATORY_FIRST
                if gain <= 0:
                    continue
                if best is None or gain > best[0]:
                    best = (gain, i, d, position, delta)
            if best is None:
                break
            _, i, d, position, _ = best
            sol.routes[d].insert(position, i)
            sol.unassigned.discard(i)
            for other in range(self.n):
                cache.pop((other, d), None)
        return sol

    # ------------------------------------------------------------ local search

    def _local_moves(self, sol: Solution) -> Iterator[Move]:
        """Every neighbouring plan, cheapest-to-evaluate kinds first."""
        days = range(self.num_days)

        # 1. Add a place that is currently left out.
        for i in sorted(sol.unassigned):
            for d in days:
                if len(sol.routes[d]) >= self.cap or self.win[d][i] is None:
                    continue
                for p in range(len(sol.routes[d]) + 1):
                    yield self.mv_insert(sol, i, d, p)

        # 2. Re-order within a day (2-opt: reverse a stretch).
        for d in days:
            n = len(sol.routes[d])
            for a in range(n - 1):
                for b in range(a + 1, n):
                    yield self.mv_two_opt(sol, d, a, b)

        # 3. Relocate or Or-opt: move 1-3 consecutive stops anywhere, either way round.
        for d1 in days:
            for length in (1, 2, 3):
                for p1 in range(len(sol.routes[d1]) - length + 1):
                    for d2 in days:
                        for p2 in range(len(sol.routes[d2]) + 1):
                            for reverse in (False, True) if length > 1 else (False,):
                                move = self.mv_relocate(
                                    sol, d1, p1, length, d2, p2, reverse
                                )
                                if move is not None:
                                    yield move

        # 4. Swap two stops, on the same day or across days.
        slots = [(d, p) for d in days for p in range(len(sol.routes[d]))]
        for x in range(len(slots)):
            for y in range(x + 1, len(slots)):
                move = self.mv_swap(sol, *slots[x], *slots[y])
                if move is not None:
                    yield move

        # 5. Swap an assigned place for one that is left out.
        for i in sorted(sol.unassigned):
            for d, p in slots:
                if self.win[d][i] is not None:
                    yield self.mv_replace(sol, i, d, p)

        # 6. Drop a place whose cost exceeds the price of leaving it out.
        for d, p in slots:
            yield self.mv_remove(sol, d, p)

    def local_search(
        self, sol: Solution, deadline: float | None = None, max_passes: int = 10_000
    ) -> Solution:
        """Apply the first improving move, repeatedly, until none helps."""
        for _ in range(max_passes):
            improved = False
            for move in self._local_moves(sol):
                if deadline is not None and time.perf_counter() > deadline:
                    return sol
                delta = self.move_delta(sol, move)
                if delta is not None and delta < -_EPSILON:
                    self.apply(sol, move)
                    improved = True
                    break
            if not improved:
                break
        return sol

    # --------------------------------------------------------------- annealing

    def _random_move(self, sol: Solution, rng: random.Random) -> Move | None:
        slots = [(d, p) for d, route in enumerate(sol.routes) for p in range(len(route))]
        if not slots and not sol.unassigned:
            return None
        roll = rng.random()

        if roll < 0.30 and slots:  # relocate / Or-opt
            d1, p1 = rng.choice(slots)
            length = rng.choice((1, 1, 1, 2, 3))
            d2 = rng.randrange(self.num_days)
            limit = len(sol.routes[d2]) - (length if d1 == d2 else 0)
            if limit < 0:
                return None
            return self.mv_relocate(
                sol, d1, p1, length, d2, rng.randint(0, limit), rng.random() < 0.5
            )
        if roll < 0.50 and len(slots) >= 2:  # swap
            (d1, p1), (d2, p2) = rng.sample(slots, 2)
            return self.mv_swap(sol, d1, p1, d2, p2)
        if roll < 0.70:  # 2-opt
            candidates = [d for d, route in enumerate(sol.routes) if len(route) >= 2]
            if not candidates:
                return None
            d = rng.choice(candidates)
            a, b = sorted(rng.sample(range(len(sol.routes[d])), 2))
            return self.mv_two_opt(sol, d, a, b)
        if roll < 0.87 and sol.unassigned:  # add
            i = rng.choice(tuple(sol.unassigned))
            d = rng.randrange(self.num_days)
            if len(sol.routes[d]) >= self.cap or self.win[d][i] is None:
                return None
            return self.mv_insert(sol, i, d, rng.randint(0, len(sol.routes[d])))
        if roll < 0.96 and slots and sol.unassigned:  # replace
            d, p = rng.choice(slots)
            i = rng.choice(tuple(sol.unassigned))
            return self.mv_replace(sol, i, d, p) if self.win[d][i] else None
        if slots:  # remove
            d, p = rng.choice(slots)
            return self.mv_remove(sol, d, p)
        return None

    def simulated_annealing(
        self,
        start: Solution,
        rng: random.Random,
        iterations: int,
        deadline: float | None = None,
    ) -> Solution:
        """Metropolis search: always accept better plans, sometimes accept worse ones."""
        current = start.copy()
        best = current.copy()
        current_value = best_value = self.objective(current)

        scale = self._typical_arc()
        t_start, t_end = max(8.0, scale), max(0.05, scale / 400.0)
        for step in range(iterations):
            if deadline is not None and step % 256 == 0 and time.perf_counter() > deadline:
                break
            temperature = t_start * (t_end / t_start) ** (step / max(1, iterations - 1))
            move = self._random_move(current, rng)
            if move is None:
                continue
            delta = self.move_delta(current, move)
            if delta is None:
                continue
            if delta <= 0 or rng.random() < math.exp(-delta / temperature):
                self.apply(current, move)
                current_value += delta
                if current_value < best_value - _EPSILON:
                    best, best_value = current.copy(), current_value
        return best

    def _typical_arc(self) -> float:
        """Mean cost of a leg between two places: the natural scale for temperature."""
        if self.n < 2:
            return 20.0
        sample = [
            self.dur[a][b] + self.service[a]
            for a in range(1, self.n + 1)
            for b in range(1, min(self.n + 1, a + 6))
            if a != b
        ]
        return sum(sample) / len(sample) if sample else 20.0

    # ----------------------------------------------------------------- genetic

    def decode(self, order: list[int]) -> Solution:
        """Turn a visiting order into a plan: each place goes on the earliest day it fits.

        Appending is checked incrementally (O(1) per attempt), which keeps the
        evolutionary loop fast. Optional places are only added when doing so costs
        less than leaving them out. Mandatory places are placed first.
        """
        sol = self.empty_solution()
        # Per-day running state: (clock at last stop, last node, km, cost, count)
        state = [(self.days[d].start_minutes, 0, 0.0, 0, 0) for d in range(self.num_days)]
        routes: list[list[int]] = [[] for _ in range(self.num_days)]

        ordered = [i for i in order if self.mandatory[i]] + [
            i for i in order if not self.mandatory[i]
        ]
        for i in ordered:
            if not self.schedulable[i]:
                continue
            node = i + 1
            for d in range(self.num_days):
                window = self.win[d][i]
                clock, prev, km, cost, count = state[d]
                if window is None or count >= self.cap:
                    continue
                arrive = clock + self.service[prev] + self.dur[prev][node]
                start = arrive if arrive >= window[0] else window[0]
                if count > 0 and start - arrive > self.max_wait:
                    continue
                if start > window[1]:
                    continue
                if (
                    start + self.service[node] + self.dur[node][0]
                    > self.days[d].end_minutes
                ):
                    continue
                new_km = km + self.dist[prev][node] + self.dist[node][0]
                if prev != 0:
                    new_km -= self.dist[prev][0]
                if new_km > self.max_km:
                    continue
                # ``cost`` includes the way home; swap that for the new way home.
                old_home = (self.service[prev] + self.dur[prev][0]) if count else 0
                added = (
                    self.service[prev]
                    + self.dur[prev][node]
                    + self.service[node]
                    + self.dur[node][0]
                    - old_home
                )
                if not self.mandatory[i] and added >= self.penalty[i]:
                    continue
                routes[d].append(i)
                state[d] = (start, node, new_km, cost + added, count + 1)
                sol.unassigned.discard(i)
                break
        sol.routes = routes
        return sol

    def genetic(
        self,
        seeds: list[list[int]],
        rng: random.Random,
        population: int,
        generations: int,
        deadline: float | None = None,
    ) -> Solution:
        """Evolve visiting orders; fitness is the objective of the decoded plan."""
        genes = [i for i in range(self.n) if self.schedulable[i]]
        if len(genes) < 2:
            return self.decode(genes)

        pool: list[list[int]] = [s[:] for s in seeds if sorted(s) == sorted(genes)]
        while len(pool) < population:
            shuffled = genes[:]
            rng.shuffle(shuffled)
            pool.append(shuffled)
        pool = pool[:population]

        def fitness(order: list[int]) -> int:
            return self.objective(self.decode(order))

        scored = sorted(((fitness(o), o) for o in pool), key=lambda t: t[0])
        elite = 2

        for generation in range(generations):
            if (
                deadline is not None
                and generation % 4 == 0
                and time.perf_counter() > deadline
            ):
                break
            next_pool = [o for _, o in scored[:elite]]
            while len(next_pool) < population:
                mother = self._tournament(scored, rng)
                father = self._tournament(scored, rng)
                child = self._order_crossover(mother, father, rng)
                if rng.random() < 0.35:
                    self._mutate(child, rng)
                next_pool.append(child)
            scored = sorted(((fitness(o), o) for o in next_pool), key=lambda t: t[0])
        return self.decode(scored[0][1])

    @staticmethod
    def _tournament(scored: list[tuple[int, list[int]]], rng: random.Random) -> list[int]:
        return min(rng.sample(scored, min(3, len(scored))), key=lambda t: t[0])[1]

    @staticmethod
    def _order_crossover(a: list[int], b: list[int], rng: random.Random) -> list[int]:
        """Keep a slice of parent ``a`` and fill the rest in the order of parent ``b``."""
        size = len(a)
        left, right = sorted(rng.sample(range(size), 2))
        kept = set(a[left : right + 1])
        fill = [g for g in b if g not in kept]
        return fill[:left] + a[left : right + 1] + fill[left:]

    @staticmethod
    def _mutate(order: list[int], rng: random.Random) -> None:
        if rng.random() < 0.5:
            x, y = rng.sample(range(len(order)), 2)
            order[x], order[y] = order[y], order[x]
        else:
            x, y = sorted(rng.sample(range(len(order)), 2))
            order[x : y + 1] = order[x : y + 1][::-1]

    # ------------------------------------------------------------------ output

    def flatten(self, sol: Solution) -> list[int]:
        """A plan as a visiting order (day by day, then the places left out)."""
        order = [i for route in sol.routes for i in route]
        order += sorted(sol.unassigned)
        return order

    def to_result(
        self, sol: Solution, engine: str, started: float, status: str
    ) -> OptimizationResult:
        """Build the schedule the rest of the app consumes."""
        days: list[DaySchedule] = []
        visited: set[int] = set()
        mode = self.request.mode

        for d, route in enumerate(sol.routes):
            schedule = DaySchedule(day_index=self.days[d].index)
            clock = self.days[d].start_minutes
            prev = 0
            for position, i in enumerate(route):
                node = i + 1
                window = self.win[d][i] or (self.days[d].start_minutes, 0)
                arrive = clock + self.service[prev] + self.dur[prev][node]
                start = max(arrive, window[0])
                leg_km = self.dist[prev][node]
                leg_mode = effective_mode(leg_km, mode)
                schedule.stops.append(
                    ScheduledStop(
                        place_index=i,
                        arrival_minutes=start,
                        departure_minutes=start + self.places[i].visit_minutes,
                        travel_km=round(leg_km, 3),
                        travel_minutes=0
                        if position == 0 and leg_km == 0
                        else travel_minutes(leg_km, leg_mode),
                        mode=leg_mode,
                    )
                )
                schedule.total_distance_km += leg_km
                visited.add(i)
                clock, prev = start, node
            schedule.total_distance_km = round(schedule.total_distance_km, 3)
            schedule.total_travel_minutes = sum(s.travel_minutes for s in schedule.stops)
            days.append(schedule)

        return OptimizationResult(
            days=days,
            dropped=[i for i in range(self.n) if i not in visited],
            status=status,
            objective_value=self.objective(sol),
            duration_seconds=round(time.perf_counter() - started, 3),
            engine=engine,
        )

    def from_result(self, result: OptimizationResult) -> Solution:
        """Re-read a schedule from another engine (OR-Tools) as a :class:`Solution`."""
        sol = self.empty_solution()
        by_index = {day.index: d for d, day in enumerate(self.days)}
        for schedule in result.days:
            d = by_index.get(schedule.day_index)
            if d is None:
                continue
            sol.routes[d] = [stop.place_index for stop in schedule.stops]
            sol.unassigned.difference_update(sol.routes[d])
        return sol


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

#: Algorithms implemented in this module (everything except OR-Tools and auto).
NATIVE_ALGORITHMS: tuple[OptimizerAlgorithm, ...] = (
    OptimizerAlgorithm.NEAREST_NEIGHBOR,
    OptimizerAlgorithm.CHEAPEST_INSERTION,
    OptimizerAlgorithm.LOCAL_SEARCH,
    OptimizerAlgorithm.SIMULATED_ANNEALING,
    OptimizerAlgorithm.GENETIC,
)

ALGORITHM_INFO: dict[OptimizerAlgorithm, dict[str, str]] = {
    OptimizerAlgorithm.AUTO: {
        "label": "Auto (best of several)",
        "speed": "slowest",
        "quality": "best available",
        "description": (
            "Runs every algorithm and keeps the plan with the lowest objective. "
            "Use when plan quality matters more than response time."
        ),
    },
    OptimizerAlgorithm.ORTOOLS: {
        "label": "OR-Tools (guided local search)",
        "speed": "slow",
        "quality": "excellent",
        "description": (
            "Google OR-Tools vehicle-routing solver with time windows, run for the "
            "full time limit. The long-standing default."
        ),
    },
    OptimizerAlgorithm.NEAREST_NEIGHBOR: {
        "label": "Nearest neighbour",
        "speed": "instant",
        "quality": "basic",
        "description": (
            "From where you are, go to the closest place that still fits. Very fast, "
            "but myopic: it can leave long detours for the last stops."
        ),
    },
    OptimizerAlgorithm.CHEAPEST_INSERTION: {
        "label": "Cheapest insertion",
        "speed": "very fast",
        "quality": "good",
        "description": (
            "Builds the whole plan by repeatedly adding the place with the best "
            "net gain at its cheapest position, across all days at once."
        ),
    },
    OptimizerAlgorithm.LOCAL_SEARCH: {
        "label": "Local search (2-opt + Or-opt)",
        "speed": "fast",
        "quality": "very good",
        "description": (
            "Cheapest insertion followed by 2-opt, Or-opt, relocate, swap and replace "
            "moves until no move improves the plan."
        ),
    },
    OptimizerAlgorithm.SIMULATED_ANNEALING: {
        "label": "Simulated annealing",
        "speed": "moderate",
        "quality": "very good",
        "description": (
            "Local search that occasionally accepts a worse plan while a "
            "temperature cools, so it can escape local optima."
        ),
    },
    OptimizerAlgorithm.GENETIC: {
        "label": "Genetic algorithm",
        "speed": "moderate",
        "quality": "good",
        "description": (
            "Evolves a population of visiting orders using order crossover, "
            "mutation and elitism."
        ),
    },
}


def solve(
    request: OptimizationRequest,
    algorithm: OptimizerAlgorithm,
    time_budget: float | None = None,
) -> OptimizationResult:
    """Run one of the native algorithms.

    Args:
        request: The itinerary to plan.
        algorithm: One of :data:`NATIVE_ALGORITHMS`.
        time_budget: Wall-clock safety limit in seconds. The randomised algorithms
            stop on an iteration count first, so results do not depend on machine
            speed; the budget only matters on unusually large inputs.

    Raises:
        ValueError: when ``algorithm`` is not a native algorithm.
    """
    if algorithm not in NATIVE_ALGORITHMS:
        raise ValueError(f"{algorithm!s} is not a native algorithm")

    started = time.perf_counter()
    budget = float(request.time_limit_seconds if time_budget is None else time_budget)
    deadline = started + max(0.05, budget)
    problem = RoutingProblem(request)
    rng = random.Random(request.seed)
    size = max(1, sum(problem.schedulable))

    if algorithm is OptimizerAlgorithm.NEAREST_NEIGHBOR:
        sol = problem.nearest_neighbor(deadline)
        status = "heuristic"
    elif algorithm is OptimizerAlgorithm.CHEAPEST_INSERTION:
        sol = problem.cheapest_insertion(deadline)
        status = "heuristic"
    elif algorithm is OptimizerAlgorithm.LOCAL_SEARCH:
        sol = problem.local_search(problem.cheapest_insertion(deadline), deadline)
        status = "local_optimum"
    elif algorithm is OptimizerAlgorithm.SIMULATED_ANNEALING:
        start = problem.local_search(problem.cheapest_insertion(deadline), deadline)
        iterations = max(3_000, min(30_000, 400 * size))
        sol = problem.simulated_annealing(start, rng, iterations, deadline)
        # Anneal can leave small slack; a final descent never makes the plan worse.
        sol = problem.local_search(sol, deadline)
        status = "annealed"
    else:  # GENETIC
        seed_plan = problem.cheapest_insertion(deadline)
        seeds = [problem.flatten(seed_plan)]
        by_score = sorted(range(problem.n), key=lambda i: -problem.places[i].score)
        seeds.append([i for i in by_score if problem.schedulable[i]])
        generations = max(40, min(150, 4 * size))
        sol = problem.genetic(seeds, rng, 30, generations, deadline)
        status = "evolved"

    return problem.to_result(sol, algorithm.value, started, status)


def solve_portfolio(
    request: OptimizationRequest,
    ortools_solver: Callable[[OptimizationRequest], OptimizationResult | None] | None,
) -> OptimizationResult:
    """Run every algorithm and return the plan with the lowest objective.

    Ties go to the faster algorithm. ``ortools_solver`` is passed in rather than
    imported so this module never depends on the engine that imports it.
    """
    started = time.perf_counter()
    problem = RoutingProblem(request)
    candidates: list[tuple[int, int, OptimizationResult]] = []

    for rank, algorithm in enumerate(NATIVE_ALGORITHMS):
        result = solve(request, algorithm, time_budget=request.time_limit_seconds)
        candidates.append((problem.objective(problem.from_result(result)), rank, result))

    if ortools_solver is not None:
        try:
            result = ortools_solver(request)
        except Exception:  # pragma: no cover - solver internals
            result = None
        if result is not None and result.feasible:
            sol = problem.from_result(result)
            candidates.append((problem.objective(sol), len(NATIVE_ALGORITHMS), result))

    objective, _, best = min(candidates, key=lambda c: (c[0], c[1]))
    best.objective_value = objective
    best.status = f"best_of_{len(candidates)}"
    best.engine = f"auto:{best.engine}"
    best.duration_seconds = round(time.perf_counter() - started, 3)
    return best


def objective_of(request: OptimizationRequest, result: OptimizationResult) -> int:
    """Score any result on the common objective, so engines can be compared fairly."""
    problem = RoutingProblem(request)
    return problem.objective(problem.from_result(result))
