"use client";

import { ArrowRight, MapPin, Sparkles, Star } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { HERO_IMAGES, blurPhoto, photo } from "@/lib/images";
import { cn } from "@/lib/utils";

const ROTATE_MS = 7000;

const QUICK_DESTINATIONS = [
  { city: "Paris", country: "France" },
  { city: "Rome", country: "Italy" },
  { city: "Tokyo", country: "Japan" },
];

/**
 * Landing hero.
 *
 * A slow cross-fade between destination photographs, with the search panel in
 * glass on top. The photograph does the selling; the copy stays short and the
 * single call to action is unambiguous.
 */
export function Hero() {
  const router = useRouter();
  const [index, setIndex] = useState(0);
  const [query, setQuery] = useState("");

  useEffect(() => {
    // Respect a reduced-motion preference by holding on the first image.
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (motionQuery.matches) return;

    const timer = setInterval(
      () => setIndex((current) => (current + 1) % HERO_IMAGES.length),
      ROTATE_MS,
    );
    return () => clearInterval(timer);
  }, []);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    router.push(
      trimmed ? `/plan?destination=${encodeURIComponent(trimmed)}` : "/plan",
    );
  }

  return (
    <section className="relative isolate min-h-[92svh] overflow-hidden">
      {/* Photography */}
      <div className="absolute inset-0 -z-10">
        {HERO_IMAGES.map((key, position) => (
          <div
            key={key}
            aria-hidden={position !== index}
            className={cn(
              "absolute inset-0 transition-opacity duration-[2000ms] ease-in-out",
              position === index ? "opacity-100" : "opacity-0",
            )}
          >
            <Image
              src={photo(key, { width: 2048, height: 1400 })}
              alt=""
              fill
              // The hero is the largest contentful paint on this page, so the
              // first frame is eager and the rest load lazily behind it.
              priority={position === 0}
              loading={position === 0 ? "eager" : "lazy"}
              sizes="100vw"
              placeholder="blur"
              blurDataURL={blurPhoto(key)}
              className="object-cover"
            />
          </div>
        ))}
        {/* Two scrims: a vertical one for the header and a bottom-weighted one
            so the panel's text stays legible whatever the photo is doing. */}
        <div className="absolute inset-0 bg-gradient-to-b from-slate-950/65 via-slate-950/25 to-slate-950/80" />
      </div>

      <div className="mx-auto flex min-h-[92svh] max-w-7xl flex-col justify-center px-4 pb-16 pt-28 sm:px-6 lg:px-8">
        <div className="max-w-3xl animate-fade-up">
          <span className="inline-flex items-center gap-2 rounded-full bg-white/15 px-4 py-1.5 text-sm font-medium text-white backdrop-blur-md ring-1 ring-white/25">
            <Sparkles className="size-4" />
            Planned by AI, scheduled by a real routing engine
          </span>

          <h1 className="mt-6 text-4xl font-extrabold leading-[1.05] text-white sm:text-5xl lg:text-7xl">
            Trips that actually
            <span className="block">fit into the day.</span>
          </h1>

          <p className="mt-6 max-w-xl text-lg leading-relaxed text-white/85 sm:text-xl">
            Tell us how you like to travel. We check opening hours, work out the
            walking between stops, and hand you an hour-by-hour plan you can
            change whenever you like.
          </p>

          {/* Search panel */}
          <form
            onSubmit={handleSubmit}
            className="glass mt-10 flex flex-col gap-3 rounded-3xl p-3 sm:flex-row sm:items-center sm:rounded-full sm:p-2.5"
          >
            <label htmlFor="hero-destination" className="sr-only">
              Where do you want to go?
            </label>
            <div className="flex flex-1 items-center gap-3 px-3 sm:px-4">
              <MapPin className="size-5 shrink-0 text-primary" />
              <input
                id="hero-destination"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Where to? Try Paris, Rome or Tokyo"
                className="h-12 w-full bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground"
                autoComplete="off"
              />
            </div>
            <Button type="submit" size="lg" className="shrink-0 sm:px-8">
              Plan my trip
              <ArrowRight />
            </Button>
          </form>

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <span className="text-sm text-white/70">Popular right now:</span>
            {QUICK_DESTINATIONS.map((destination) => (
              <button
                key={destination.city}
                type="button"
                onClick={() =>
                  router.push(
                    `/plan?destination=${encodeURIComponent(destination.city)}`,
                  )
                }
                className="rounded-full bg-white/15 px-3.5 py-1.5 text-sm font-medium text-white backdrop-blur-md transition-colors hover:bg-white/25"
              >
                {destination.city}
              </button>
            ))}
          </div>
        </div>

        {/* Trust strip */}
        <dl className="mt-14 grid max-w-3xl grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
          {[
            { value: "4.9", label: "Average rating", star: true },
            { value: "38k+", label: "Itineraries planned" },
            { value: "120+", label: "Cities covered" },
            { value: "28%", label: "Less walking per day" },
          ].map((stat) => (
            <div key={stat.label}>
              <dt className="sr-only">{stat.label}</dt>
              <dd className="flex items-baseline gap-1.5 text-2xl font-bold text-white sm:text-3xl">
                {stat.value}
                {stat.star && (
                  <Star className="size-5 fill-star text-star" aria-hidden />
                )}
              </dd>
              <p className="mt-0.5 text-sm text-white/70">{stat.label}</p>
            </div>
          ))}
        </dl>
      </div>

      {/* Which photograph is showing */}
      <div className="absolute bottom-6 left-1/2 flex -translate-x-1/2 gap-2">
        {HERO_IMAGES.map((key, position) => (
          <button
            key={key}
            type="button"
            onClick={() => setIndex(position)}
            aria-label={`Show photograph ${position + 1}`}
            aria-current={position === index}
            className={cn(
              "h-1.5 rounded-full transition-all duration-300",
              position === index
                ? "w-8 bg-white"
                : "w-1.5 bg-white/45 hover:bg-white/70",
            )}
          />
        ))}
      </div>
    </section>
  );
}
