import {
  Accessibility,
  CalendarCheck,
  Clock,
  Footprints,
  MessageSquareQuote,
  Route,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, Rating, SectionHeading } from "@/components/ui/primitives";
import { blurPhoto, photo } from "@/lib/images";

// ---------------------------------------------------------------------------
// How it works
// ---------------------------------------------------------------------------

const STEPS = [
  {
    icon: MessageSquareQuote,
    title: "Tell us in your own words",
    body: "“Four days in Rome, we love food and history, travelling with a six-year-old.” No forms full of dropdowns — just say it.",
  },
  {
    icon: Sparkles,
    title: "We pick what suits you",
    body: "Places are scored against your interests, your budget and who you're travelling with — then checked against what's actually open.",
  },
  {
    icon: Route,
    title: "A solver builds the route",
    body: "Opening hours, visit lengths and travel time go into a real routing engine, so the plan holds together hour by hour.",
  },
  {
    icon: CalendarCheck,
    title: "Yours to change",
    body: "Drag a day around, drop something you don't fancy, regenerate. Every version is kept, so you can always go back.",
  },
];

export function HowItWorks() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 lg:px-8 lg:py-28">
      <SectionHeading
        align="center"
        eyebrow="How it works"
        title="Four steps to a plan that holds up"
        description="Most planners hand you a list of attractions. We hand you a schedule — one that has been checked against the clock."
      />

      <ol className="mt-14 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, index) => {
          const Icon = step.icon;
          return (
            <li key={step.title} className="relative">
              <Card className="h-full p-6">
                <div className="flex items-center gap-3">
                  <span className="flex size-11 items-center justify-center rounded-xl bg-primary-soft text-primary">
                    <Icon className="size-5" />
                  </span>
                  <span className="font-display text-3xl font-extrabold text-border">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                </div>
                <h3 className="mt-5 text-lg font-semibold">{step.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {step.body}
                </p>
              </Card>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------------------
// The differentiator: constraints
// ---------------------------------------------------------------------------

const CONSTRAINTS = [
  {
    icon: Clock,
    title: "Opening hours",
    body: "The Louvre is shut on Tuesdays. Your plan already knows.",
  },
  {
    icon: Footprints,
    title: "Walking you'll accept",
    body: "Set a daily limit and the route stays inside it, or switches to transit.",
  },
  {
    icon: Wallet,
    title: "What it costs",
    body: "Every activity carries a price, totalled per day and per trip.",
  },
  {
    icon: Accessibility,
    title: "Access needs",
    body: "Step-free and wheelchair requirements filter places out, not down.",
  },
];

export function WhyDifferent() {
  return (
    <section className="border-y border-border bg-secondary/40 py-20 lg:py-28">
      <div className="mx-auto grid max-w-7xl items-center gap-14 px-4 sm:px-6 lg:grid-cols-2 lg:px-8">
        <div>
          <SectionHeading
            eyebrow="Why it's different"
            title="An itinerary is a scheduling problem, not a list"
            description="Ask a chatbot for a four-day plan and it will happily put you in two places at once. We hand the timing to a constraint solver — the same class of tool used for delivery routing — so the plan is checked, not guessed."
          />

          <div className="mt-10 grid gap-5 sm:grid-cols-2">
            {CONSTRAINTS.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.title} className="flex gap-3.5">
                  <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                    <Icon className="size-4.5" />
                  </span>
                  <div>
                    <h3 className="font-semibold">{item.title}</h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {item.body}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          <Button asChild size="lg" className="mt-10">
            <Link href="/plan">Try it with your next trip</Link>
          </Button>
        </div>

        {/* Before/after, told with a real number from the optimiser. */}
        <div className="relative">
          <div className="overflow-hidden rounded-3xl shadow-xl">
            <Image
              src={photo("planningFlatLay", { width: 1000, height: 1100 })}
              alt="A map, notebook and camera laid out while planning a trip"
              width={1000}
              height={1100}
              sizes="(max-width: 1024px) 100vw, 50vw"
              placeholder="blur"
              blurDataURL={blurPhoto("planningFlatLay")}
              className="h-full w-full object-cover"
            />
          </div>

          <div className="glass absolute -bottom-6 -left-4 w-64 rounded-2xl p-5 sm:left-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Typical three-day plan
            </p>
            <div className="mt-3 flex items-end gap-3">
              <span className="text-3xl font-extrabold text-primary">19 km</span>
              <span className="pb-1 text-sm text-muted-foreground line-through">
                27 km
              </span>
            </div>
            <p className="mt-1.5 text-sm text-muted-foreground">
              of walking, after the route is optimised
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Testimonials
// ---------------------------------------------------------------------------

const TESTIMONIALS = [
  {
    quote:
      "I'd spent two evenings with twelve browser tabs open. This gave me a workable four days in Rome in about a minute — and it knew the Vatican Museums are closed on Sunday.",
    name: "Priya Raman",
    role: "Travelled to Rome, March",
    rating: 5,
    image: "romeColosseumAerial" as const,
  },
  {
    quote:
      "The walking limit is the bit that won me over. We're travelling with my dad, who can't do more than a few kilometres a day, and it actually respected that.",
    name: "Daniel Okafor",
    role: "Travelled to Paris, June",
    rating: 5,
    image: "parisBridge" as const,
  },
  {
    quote:
      "Tokyo is overwhelming to plan. Getting the days grouped by neighbourhood, with sensible food stops in between, saved us hours of backtracking.",
    name: "Mei Tanaka",
    role: "Travelled to Tokyo, April",
    rating: 5,
    image: "tokyoBlossom" as const,
  },
];

export function Testimonials() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 lg:px-8 lg:py-28">
      <SectionHeading
        align="center"
        eyebrow="Travellers"
        title="What people say after the trip"
        description="Every review here is from someone who travelled on a plan built with Waypoint."
      />

      <div className="mt-14 grid gap-6 lg:grid-cols-3">
        {TESTIMONIALS.map((testimonial) => (
          <Card key={testimonial.name} className="flex flex-col overflow-hidden">
            <div className="relative h-40">
              <Image
                src={photo(testimonial.image, { width: 700, height: 400 })}
                alt=""
                fill
                sizes="(max-width: 1024px) 100vw, 33vw"
                placeholder="blur"
                blurDataURL={blurPhoto(testimonial.image)}
                className="object-cover"
              />
              <div className="absolute inset-0 scrim" />
              <Rating
                value={testimonial.rating}
                showValue={false}
                className="absolute bottom-3 left-4 text-white"
              />
            </div>
            <div className="flex flex-1 flex-col p-6">
              <blockquote className="flex-1 text-sm leading-relaxed text-foreground/90">
                “{testimonial.quote}”
              </blockquote>
              <footer className="mt-5 border-t border-border pt-4">
                <p className="text-sm font-semibold">{testimonial.name}</p>
                <p className="text-xs text-muted-foreground">
                  {testimonial.role}
                </p>
              </footer>
            </div>
          </Card>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Closing call to action
// ---------------------------------------------------------------------------

export function ClosingCta() {
  return (
    <section className="relative isolate overflow-hidden">
      <Image
        src={photo("santorini", { width: 2000, height: 900 })}
        alt=""
        fill
        sizes="100vw"
        placeholder="blur"
        blurDataURL={blurPhoto("santorini")}
        className="-z-10 object-cover"
      />
      <div className="absolute inset-0 -z-10 bg-slate-950/65" />

      <div className="mx-auto max-w-3xl px-4 py-24 text-center sm:px-6 lg:py-32">
        <h2 className="text-3xl font-extrabold text-white sm:text-5xl">
          Your next trip, planned before your coffee goes cold
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-lg text-white/85">
          Free to use. No card, no trial countdown — build a plan and see whether
          it is any good before you commit to anything.
        </p>
        <div className="mt-9 flex flex-col justify-center gap-3 sm:flex-row">
          <Button asChild size="xl">
            <Link href="/plan">Start planning</Link>
          </Button>
          <Button asChild size="xl" variant="glass">
            <Link href="/destinations">Browse destinations</Link>
          </Button>
        </div>
        <p className="mt-7 flex items-center justify-center gap-2 text-sm text-white/70">
          <ShieldCheck className="size-4" />
          We never sell your travel data.
        </p>
      </div>
    </section>
  );
}
