"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Baby,
  Check,
  Loader2,
  MapPin,
  Minus,
  Plus,
  Search,
  Sparkles,
  Users,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { CityBackdrop } from "@/components/places/city-backdrop";
import { Button } from "@/components/ui/button";
import { Progress, Slider } from "@/components/ui/controls";
import {
  Badge,
  Card,
  Field,
  Input,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { ApiError, api } from "@/lib/api";
import {
  addDays,
  cn,
  daysBetween,
  normalizeSearch,
  toISODate,
} from "@/lib/utils";
import { useAuthStore } from "@/store/auth";
import type {
  AccessibilityNeed,
  BudgetLevel,
  CitySummary,
  InterestTag,
  TransportMode,
  TravelPace,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Option catalogues
// ---------------------------------------------------------------------------

const INTERESTS: { value: InterestTag; label: string; emoji: string }[] = [
  { value: "history", label: "History", emoji: "🏛️" },
  { value: "art", label: "Art", emoji: "🎨" },
  { value: "food", label: "Food", emoji: "🍜" },
  { value: "nature", label: "Nature", emoji: "🌿" },
  { value: "architecture", label: "Architecture", emoji: "🏗️" },
  { value: "culture", label: "Culture", emoji: "🎭" },
  { value: "nightlife", label: "Nightlife", emoji: "🌃" },
  { value: "shopping", label: "Shopping", emoji: "🛍️" },
  { value: "photography", label: "Photography", emoji: "📷" },
  { value: "relaxation", label: "Relaxation", emoji: "🧘" },
  { value: "family", label: "Family", emoji: "👨‍👩‍👧" },
  { value: "adventure", label: "Adventure", emoji: "🧗" },
  { value: "music", label: "Music", emoji: "🎵" },
  { value: "sports", label: "Sports", emoji: "⚽" },
];

const PACES: { value: TravelPace; label: string; detail: string }[] = [
  { value: "relaxed", label: "Relaxed", detail: "About 3 stops a day" },
  { value: "moderate", label: "Balanced", detail: "About 5 stops a day" },
  { value: "packed", label: "Packed", detail: "About 7 stops a day" },
];

const BUDGETS: { value: BudgetLevel; label: string; detail: string }[] = [
  { value: "shoestring", label: "Shoestring", detail: "Free and cheap" },
  { value: "budget", label: "Budget", detail: "Watching the spend" },
  { value: "moderate", label: "Mid-range", detail: "Comfortable" },
  { value: "comfortable", label: "Comfortable", detail: "Room to splash out" },
  { value: "luxury", label: "Luxury", detail: "Best of everything" },
];

const TRANSPORT: { value: TransportMode; label: string; emoji: string }[] = [
  { value: "walking", label: "Walking", emoji: "🚶" },
  { value: "transit", label: "Public transport", emoji: "🚇" },
  { value: "cycling", label: "Cycling", emoji: "🚲" },
  { value: "driving", label: "Driving", emoji: "🚗" },
];

const ACCESS: { value: AccessibilityNeed; label: string }[] = [
  { value: "wheelchair", label: "Wheelchair access" },
  { value: "step_free", label: "Step-free routes" },
  { value: "low_walking", label: "Minimal walking" },
  { value: "hearing", label: "Hearing support" },
  { value: "visual", label: "Visual support" },
];

const STEPS = ["Where & when", "Who's going", "What you like", "Anything else"];

// ---------------------------------------------------------------------------
// Wizard state
// ---------------------------------------------------------------------------

interface PlanState {
  destination: CitySummary | null;
  startDate: string;
  endDate: string;
  adults: number;
  children: number;
  infants: number;
  pace: TravelPace;
  budget: BudgetLevel;
  interests: InterestTag[];
  transport: TransportMode;
  maxWalkingKm: number;
  access: AccessibilityNeed[];
  dayStart: string;
  dayEnd: string;
  brief: string;
}

function defaultState(): PlanState {
  const start = addDays(new Date(), 21);
  return {
    destination: null,
    startDate: toISODate(start),
    endDate: toISODate(addDays(start, 2)),
    adults: 2,
    children: 0,
    infants: 0,
    pace: "moderate",
    budget: "moderate",
    interests: [],
    transport: "walking",
    maxWalkingKm: 8,
    access: [],
    dayStart: "09:00",
    dayEnd: "20:00",
    brief: "",
  };
}

export function PlanWizard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated, isLoading: authLoading, user } = useAuthStore();

  const [step, setStep] = useState(0);
  const [state, setState] = useState<PlanState>(defaultState);

  const patch = (changes: Partial<PlanState>) =>
    setState((current) => ({ ...current, ...changes }));

  const { data: cities, isLoading: citiesLoading } = useQuery({
    queryKey: ["cities"],
    queryFn: () => api.places.cities(),
    staleTime: 10 * 60_000,
  });

  // Seed from the landing-page search box, so a destination typed there is not
  // asked for a second time.
  const prefill = searchParams.get("destination");
  useEffect(() => {
    if (!prefill || !cities || state.destination) return;
    const match = cities.find(
      (city) => city.city.toLowerCase() === prefill.trim().toLowerCase(),
    );
    // One-time seed from the URL once the async city list has loaded.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (match) patch({ destination: match });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill, cities]);

  // A returning traveller's saved preferences become the starting point.
  useEffect(() => {
    if (!user) return;
    const prefs = user.preferences;
    // Seeds the form from the saved profile when it arrives (async), not on every render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((current) => ({
      ...current,
      interests: current.interests.length ? current.interests : prefs.interests,
      pace: prefs.pace,
      budget: prefs.budget_level,
      transport: prefs.preferred_transport,
      maxWalkingKm: prefs.max_daily_walking_km,
      access: prefs.accessibility_needs,
      dayStart: prefs.day_start_time,
      dayEnd: prefs.day_end_time,
    }));
  }, [user]);

  const nights = daysBetween(state.startDate, state.endDate);

  const generate = useMutation({
    async mutationFn() {
      if (!state.destination) throw new Error("Pick a destination first.");
      const { city, country, latitude, longitude } = state.destination;

      const trip = await api.trips.create({
        title: `${nights}-day trip to ${city}`,
        destination: {
          city,
          country,
          latitude: latitude ?? 0,
          longitude: longitude ?? 0,
        },
        start_date: state.startDate,
        end_date: state.endDate,
        party: {
          adults: state.adults,
          children: state.children,
          infants: state.infants,
        },
        constraints: {
          pace: state.pace,
          budget_level: state.budget,
          interests: state.interests,
          preferred_transport: state.transport,
          accessibility_needs: state.access,
          day_start_time: state.dayStart,
          day_end_time: state.dayEnd,
          max_daily_walking_km: state.maxWalkingKm,
          currency: "EUR",
          notes: state.brief.trim() || null,
        },
      });

      await api.trips.generate(trip.id, { brief: state.brief.trim() });
      return trip.id;
    },
    onSuccess(tripId) {
      toast.success("Your itinerary is ready.");
      router.push(`/trips/${tripId}`);
    },
    onError(error) {
      if (error instanceof ApiError && error.code === "optimization_failed") {
        toast.error(error.message, { duration: 8000 });
        return;
      }
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not build your itinerary. Please try again.",
      );
    },
  });

  const canContinue = [
    Boolean(state.destination) && nights >= 1 && nights <= 30,
    state.adults >= 1,
    true,
    true,
  ][step];

  function handleGenerate() {
    if (!isAuthenticated) {
      // Nothing is lost by signing in here — the wizard state is rebuilt from
      // the URL, and the plan is one click away afterwards.
      router.push(`/sign-in?next=${encodeURIComponent("/plan")}`);
      return;
    }
    generate.mutate();
  }

  if (generate.isPending) {
    return <GeneratingState destination={state.destination?.city ?? ""} />;
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 lg:py-14">
      {/* Progress */}
      <div className="mb-10">
        <div className="mb-3 flex items-center justify-between text-sm">
          <span className="font-semibold">{STEPS[step]}</span>
          <span className="text-muted-foreground">
            Step {step + 1} of {STEPS.length}
          </span>
        </div>
        <Progress value={((step + 1) / STEPS.length) * 100} />
      </div>

      <div key={step} className="animate-fade-up">
        {step === 0 && (
          <StepDestination
            state={state}
            patch={patch}
            cities={cities ?? []}
            loading={citiesLoading}
            nights={nights}
          />
        )}
        {step === 1 && <StepParty state={state} patch={patch} />}
        {step === 2 && <StepPreferences state={state} patch={patch} />}
        {step === 3 && (
          <StepBrief
            state={state}
            patch={patch}
            nights={nights}
            isAuthenticated={isAuthenticated}
            authLoading={authLoading}
          />
        )}
      </div>

      {/* Navigation */}
      <div className="mt-10 flex items-center justify-between gap-3 border-t border-border pt-6">
        <Button
          variant="ghost"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
        >
          <ArrowLeft />
          Back
        </Button>

        {step < STEPS.length - 1 ? (
          <Button
            size="lg"
            onClick={() => setStep((s) => s + 1)}
            disabled={!canContinue}
          >
            Continue
            <ArrowRight />
          </Button>
        ) : (
          <Button size="lg" onClick={handleGenerate}>
            <Sparkles />
            {isAuthenticated ? "Build my itinerary" : "Sign in and build it"}
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — destination and dates
// ---------------------------------------------------------------------------

function StepDestination({
  state,
  patch,
  cities,
  loading,
  nights,
}: {
  state: PlanState;
  patch: (changes: Partial<PlanState>) => void;
  cities: CitySummary[];
  loading: boolean;
  nights: number;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const needle = normalizeSearch(query.trim());
    if (!needle) return cities;
    return cities.filter(
      (city) =>
        normalizeSearch(city.city).includes(needle) ||
        normalizeSearch(city.country).includes(needle),
    );
  }, [cities, query]);

  const today = toISODate(new Date());

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-extrabold">Where are you going?</h1>
        <p className="mt-2 text-muted-foreground">
          These are the cities we hold detailed data for — opening hours, visit
          lengths and coordinates for every place.
        </p>
      </header>

      <div className="relative">
        <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search cities"
          className="pl-11"
          aria-label="Search destinations"
        />
      </div>

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-32 rounded-2xl" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
          No match for “{query}”. We cover {cities.length} cities so far, so
          try another spelling or the country name. More are being added.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {filtered.map((city) => {
            const selected = state.destination?.city === city.city;
            return (
              <button
                key={`${city.city}-${city.country}`}
                type="button"
                onClick={() => patch({ destination: city })}
                aria-pressed={selected}
                className={cn(
                  "group relative h-32 overflow-hidden rounded-2xl text-left transition-all",
                  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                  selected
                    ? "ring-2 ring-primary ring-offset-2"
                    : "hover:-translate-y-0.5 hover:shadow-md",
                )}
              >
                <CityBackdrop
                  city={city.city}
                  sizes="(max-width: 640px) 100vw, 50vw"
                  className="object-cover transition-transform duration-500 group-hover:scale-105"
                />
                <div className="absolute inset-0 scrim" />
                {selected && (
                  <span className="absolute right-3 top-3 flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground">
                    <Check className="size-3.5" strokeWidth={3} />
                  </span>
                )}
                <div className="absolute inset-x-0 bottom-0 p-4 text-white">
                  <p className="text-lg font-bold">{city.city}</p>
                  <p className="text-xs text-white/80">
                    {city.country} · {city.place_count} places
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Arriving" htmlFor="start-date">
          <Input
            id="start-date"
            type="date"
            min={today}
            value={state.startDate}
            onChange={(event) => {
              const startDate = event.target.value;
              // Keep the range valid: pulling the start past the end would
              // otherwise submit a trip the API rejects.
              const endDate =
                startDate > state.endDate
                  ? toISODate(addDays(new Date(startDate), 2))
                  : state.endDate;
              patch({ startDate, endDate });
            }}
          />
        </Field>
        <Field
          label="Leaving"
          htmlFor="end-date"
          error={
            nights > 30 ? "We can plan up to 30 days at a time." : undefined
          }
        >
          <Input
            id="end-date"
            type="date"
            min={state.startDate}
            value={state.endDate}
            onChange={(event) => patch({ endDate: event.target.value })}
          />
        </Field>
      </div>

      {nights >= 1 && nights <= 30 && (
        <p className="flex items-center gap-2 rounded-xl bg-primary-soft px-4 py-3 text-sm text-primary">
          <MapPin className="size-4" />
          {nights} {nights === 1 ? "day" : "days"}
          {state.destination ? ` in ${state.destination.city}` : ""}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — party
// ---------------------------------------------------------------------------

function Counter({
  label,
  hint,
  icon,
  value,
  onChange,
  min = 0,
  max = 20,
}: {
  label: string;
  hint: string;
  icon: React.ReactNode;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-5 last:border-0">
      <div className="flex items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-xl bg-secondary text-muted-foreground [&_svg]:size-5">
          {icon}
        </span>
        <div>
          <p className="font-medium">{label}</p>
          <p className="text-sm text-muted-foreground">{hint}</p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => onChange(Math.max(min, value - 1))}
          disabled={value <= min}
          aria-label={`Fewer ${label.toLowerCase()}`}
          className="flex size-9 items-center justify-center rounded-full border border-border transition-colors hover:border-foreground disabled:opacity-40 disabled:hover:border-border"
        >
          <Minus className="size-4" />
        </button>
        <span className="w-6 text-center font-semibold tabular-nums" aria-live="polite">
          {value}
        </span>
        <button
          type="button"
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
          aria-label={`More ${label.toLowerCase()}`}
          className="flex size-9 items-center justify-center rounded-full border border-border transition-colors hover:border-foreground disabled:opacity-40 disabled:hover:border-border"
        >
          <Plus className="size-4" />
        </button>
      </div>
    </div>
  );
}

function StepParty({
  state,
  patch,
}: {
  state: PlanState;
  patch: (changes: Partial<PlanState>) => void;
}) {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-extrabold">Who&apos;s coming?</h1>
        <p className="mt-2 text-muted-foreground">
          Travelling with children changes what we suggest and how long we leave
          between stops.
        </p>
      </header>

      <Card className="px-6 py-1">
        <Counter
          label="Adults"
          hint="13 and over"
          icon={<Users />}
          value={state.adults}
          onChange={(adults) => patch({ adults })}
          min={1}
        />
        <Counter
          label="Children"
          hint="Ages 2 to 12"
          icon={<Users />}
          value={state.children}
          onChange={(children) => patch({ children })}
        />
        <Counter
          label="Infants"
          hint="Under 2"
          icon={<Baby />}
          value={state.infants}
          onChange={(infants) => patch({ infants })}
          max={10}
        />
      </Card>

      <fieldset>
        <legend className="text-lg font-semibold">
          How full do you want the days?
        </legend>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {PACES.map((pace) => (
            <button
              key={pace.value}
              type="button"
              onClick={() => patch({ pace: pace.value })}
              aria-pressed={state.pace === pace.value}
              className={cn(
                "rounded-2xl border p-5 text-left transition-all",
                state.pace === pace.value
                  ? "border-primary bg-primary-soft"
                  : "border-border hover:border-foreground/25",
              )}
            >
              <p className="font-semibold">{pace.label}</p>
              <p className="mt-1 text-sm text-muted-foreground">{pace.detail}</p>
            </button>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — preferences
// ---------------------------------------------------------------------------

function StepPreferences({
  state,
  patch,
}: {
  state: PlanState;
  patch: (changes: Partial<PlanState>) => void;
}) {
  function toggleInterest(value: InterestTag) {
    patch({
      interests: state.interests.includes(value)
        ? state.interests.filter((i) => i !== value)
        : [...state.interests, value],
    });
  }

  function toggleAccess(value: AccessibilityNeed) {
    patch({
      access: state.access.includes(value)
        ? state.access.filter((a) => a !== value)
        : [...state.access, value],
    });
  }

  return (
    <div className="space-y-9">
      <header>
        <h1 className="text-3xl font-extrabold">What do you enjoy?</h1>
        <p className="mt-2 text-muted-foreground">
          Pick as many as you like. These decide what gets suggested — and what
          gets left out.
        </p>
      </header>

      <fieldset>
        <legend className="sr-only">Interests</legend>
        <div className="flex flex-wrap gap-2.5">
          {INTERESTS.map((interest) => {
            const active = state.interests.includes(interest.value);
            return (
              <button
                key={interest.value}
                type="button"
                onClick={() => toggleInterest(interest.value)}
                aria-pressed={active}
                className={cn(
                  "inline-flex items-center gap-2 rounded-full border px-4 py-2.5 text-sm font-medium transition-all",
                  active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:border-foreground/25 hover:bg-secondary",
                )}
              >
                <span aria-hidden>{interest.emoji}</span>
                {interest.label}
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-lg font-semibold">Budget</legend>
        <div className="mt-4 grid gap-2.5 sm:grid-cols-5">
          {BUDGETS.map((budget) => (
            <button
              key={budget.value}
              type="button"
              onClick={() => patch({ budget: budget.value })}
              aria-pressed={state.budget === budget.value}
              className={cn(
                "rounded-xl border p-3.5 text-center transition-all",
                state.budget === budget.value
                  ? "border-primary bg-primary-soft"
                  : "border-border hover:border-foreground/25",
              )}
            >
              <p className="text-sm font-semibold">{budget.label}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {budget.detail}
              </p>
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-lg font-semibold">Getting around</legend>
        <div className="mt-4 grid gap-2.5 sm:grid-cols-4">
          {TRANSPORT.map((mode) => (
            <button
              key={mode.value}
              type="button"
              onClick={() => patch({ transport: mode.value })}
              aria-pressed={state.transport === mode.value}
              className={cn(
                "flex items-center gap-2.5 rounded-xl border px-4 py-3 text-sm font-medium transition-all",
                state.transport === mode.value
                  ? "border-primary bg-primary-soft"
                  : "border-border hover:border-foreground/25",
              )}
            >
              <span aria-hidden>{mode.emoji}</span>
              {mode.label}
            </button>
          ))}
        </div>
      </fieldset>

      <div>
        <div className="flex items-baseline justify-between">
          <label htmlFor="walking" className="text-lg font-semibold">
            Most you&apos;ll walk in a day
          </label>
          <span className="font-semibold text-primary tabular-nums">
            {state.maxWalkingKm} km
          </span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          A hard limit — the route is built to stay inside it.
        </p>
        <Slider
          id="walking"
          className="mt-4"
          min={1}
          max={25}
          step={1}
          value={[state.maxWalkingKm]}
          onValueChange={([value]) => patch({ maxWalkingKm: value })}
          aria-label="Maximum walking per day in kilometres"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Days start at" htmlFor="day-start">
          <Input
            id="day-start"
            type="time"
            value={state.dayStart}
            onChange={(event) => patch({ dayStart: event.target.value })}
          />
        </Field>
        <Field label="Days end by" htmlFor="day-end">
          <Input
            id="day-end"
            type="time"
            value={state.dayEnd}
            onChange={(event) => patch({ dayEnd: event.target.value })}
          />
        </Field>
      </div>

      <fieldset>
        <legend className="text-lg font-semibold">Access needs</legend>
        <p className="mt-1 text-sm text-muted-foreground">
          These filter places out entirely, rather than just ranking them lower.
        </p>
        <div className="mt-4 flex flex-wrap gap-2.5">
          {ACCESS.map((need) => {
            const active = state.access.includes(need.value);
            return (
              <button
                key={need.value}
                type="button"
                onClick={() => toggleAccess(need.value)}
                aria-pressed={active}
                className={cn(
                  "rounded-full border px-4 py-2.5 text-sm font-medium transition-all",
                  active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:border-foreground/25 hover:bg-secondary",
                )}
              >
                {need.label}
              </button>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — free text brief
// ---------------------------------------------------------------------------

const BRIEF_EXAMPLES = [
  "We love wandering markets and eating well. One big museum is plenty — after that we'd rather be outside.",
  "First time here. We want the famous things, but not queuing for three hours. Mornings early, evenings slow.",
  "Travelling with a six-year-old. Short hops, plenty of parks, and somewhere to sit down every couple of hours.",
];

function StepBrief({
  state,
  patch,
  nights,
  isAuthenticated,
  authLoading,
}: {
  state: PlanState;
  patch: (changes: Partial<PlanState>) => void;
  nights: number;
  isAuthenticated: boolean;
  authLoading: boolean;
}) {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-extrabold">Anything else we should know?</h1>
        <p className="mt-2 text-muted-foreground">
          Optional, but this is where the plan gets personal. Write it however
          you&apos;d say it out loud.
        </p>
      </header>

      <Field htmlFor="brief" label="In your own words">
        <Textarea
          id="brief"
          value={state.brief}
          onChange={(event) => patch({ brief: event.target.value })}
          placeholder="We're celebrating an anniversary and would like one really special dinner…"
          className="min-h-36"
          maxLength={4000}
        />
      </Field>

      <div>
        <p className="text-sm font-medium text-muted-foreground">
          Not sure what to say? Try one of these:
        </p>
        <div className="mt-3 space-y-2">
          {BRIEF_EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => patch({ brief: example })}
              className="w-full rounded-xl border border-border px-4 py-3 text-left text-sm text-muted-foreground transition-colors hover:border-primary hover:bg-primary-soft hover:text-foreground"
            >
              “{example}”
            </button>
          ))}
        </div>
      </div>

      {/* Summary, so nothing is a surprise at the point of commitment. */}
      <Card className="bg-secondary/50 p-6">
        <h2 className="font-semibold">Your trip so far</h2>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Destination</dt>
            <dd className="font-medium">
              {state.destination
                ? `${state.destination.city}, ${state.destination.country}`
                : "Not chosen"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Length</dt>
            <dd className="font-medium">{nights} days</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Travellers</dt>
            <dd className="font-medium">
              {state.adults} adult{state.adults === 1 ? "" : "s"}
              {state.children > 0 && `, ${state.children} children`}
              {state.infants > 0 && `, ${state.infants} infants`}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Pace</dt>
            <dd className="font-medium capitalize">{state.pace}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-muted-foreground">Interests</dt>
            <dd className="mt-1.5 flex flex-wrap gap-1.5">
              {state.interests.length === 0 ? (
                <span className="text-muted-foreground">
                  None picked — we&apos;ll go with the classics
                </span>
              ) : (
                state.interests.map((interest) => (
                  <Badge key={interest} variant="primary" size="sm">
                    {INTERESTS.find((i) => i.value === interest)?.label ?? interest}
                  </Badge>
                ))
              )}
            </dd>
          </div>
        </dl>
      </Card>

      {!isAuthenticated && !authLoading && (
        <p className="rounded-xl bg-accent-soft px-4 py-3 text-sm text-accent-foreground">
          You&apos;ll be asked to sign in before we build the plan — that&apos;s how it
          gets saved to your account.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generating
// ---------------------------------------------------------------------------

const GENERATION_STAGES = [
  "Reading what you told us",
  "Finding places that fit",
  "Scoring them against your interests",
  "Solving the route and the timings",
  "Writing it up",
];

/**
 * The solve genuinely takes several seconds, so the wait is narrated rather
 * than hidden behind a spinner. Naming the stage the pipeline is really in
 * makes the delay feel purposeful instead of broken.
 */
function GeneratingState({ destination }: { destination: string }) {
  const [stage, setStage] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setStage((current) => Math.min(GENERATION_STAGES.length - 1, current + 1));
    }, 2200);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="mx-auto flex min-h-[70svh] max-w-lg flex-col items-center justify-center px-4 text-center">
      <span className="flex size-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
        <Loader2 className="size-8 animate-spin" />
      </span>
      <h1 className="mt-7 text-2xl font-bold">
        Building your {destination} itinerary
      </h1>
      <p className="mt-2 text-muted-foreground">
        This takes a few seconds — we&apos;re solving a real routing problem, not
        guessing.
      </p>

      <ol className="mt-9 w-full space-y-3 text-left">
        {GENERATION_STAGES.map((label, index) => (
          <li
            key={label}
            className={cn(
              "flex items-center gap-3 rounded-xl px-4 py-3 text-sm transition-all",
              index < stage && "text-muted-foreground",
              index === stage && "bg-primary-soft font-medium text-primary",
              index > stage && "text-muted-foreground/50",
            )}
          >
            {index < stage ? (
              <Check className="size-4 shrink-0 text-success" strokeWidth={3} />
            ) : index === stage ? (
              <Loader2 className="size-4 shrink-0 animate-spin" />
            ) : (
              <span className="size-4 shrink-0 rounded-full border border-current" />
            )}
            {label}
          </li>
        ))}
      </ol>
    </div>
  );
}
