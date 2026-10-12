import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge class names, with later Tailwind utilities beating earlier ones.
 *
 * Plain string concatenation loses here: `"p-2" + "p-4"` leaves both classes in
 * the markup and the winner depends on stylesheet order, not call order.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/** "14:30" → "2:30 PM" (or 24-hour, following the viewer's locale). */
export function formatTime(clock: string, locale?: string): string {
  const [hours, minutes] = clock.split(":").map(Number);
  if (Number.isNaN(hours) || Number.isNaN(minutes)) return clock;
  const date = new Date(2000, 0, 1, hours, minutes);
  return new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

/** 135 → "2h 15m". Minutes are dropped when the duration is whole hours. */
export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

export function formatDistance(km: number, locale?: string): string {
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(km)} km`;
}

export function formatMoney(
  amount: number,
  currency = "USD",
  locale?: string,
): string {
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    // An unknown currency code should not blank out a price.
    return `${amount.toFixed(2)} ${currency}`;
  }
}

/** ISO date → "Fri, 12 Sep". Dates are parsed as local, not UTC. */
export function formatDate(
  iso: string,
  options: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" },
  locale?: string,
): string {
  const date = parseLocalDate(iso);
  if (!date) return iso;
  return new Intl.DateTimeFormat(locale, options).format(date);
}

export function formatDateRange(
  startIso: string,
  endIso: string,
  locale?: string,
): string {
  const start = parseLocalDate(startIso);
  const end = parseLocalDate(endIso);
  if (!start || !end) return `${startIso} – ${endIso}`;

  const sameMonth =
    start.getMonth() === end.getMonth() && start.getFullYear() === end.getFullYear();

  const startText = new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: sameMonth ? undefined : "short",
  }).format(start);
  const endText = new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    year: start.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
  }).format(end);

  return `${startText} – ${endText}`;
}

/**
 * Parse a "YYYY-MM-DD" as a *local* date.
 *
 * `new Date("2026-09-12")` is parsed as UTC midnight, which renders as the 11th
 * for anyone west of Greenwich — an off-by-one-day bug that only shows up for
 * some users, in some timezones.
 */
export function parseLocalDate(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) {
    const fallback = new Date(iso);
    return Number.isNaN(fallback.getTime()) ? null : fallback;
  }
  const [, year, month, day] = match;
  return new Date(Number(year), Number(month) - 1, Number(day));
}

export function toISODate(date: Date): string {
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

export function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function daysBetween(startIso: string, endIso: string): number {
  const start = parseLocalDate(startIso);
  const end = parseLocalDate(endIso);
  if (!start || !end) return 0;
  // Inclusive: a trip from the 1st to the 3rd is three days.
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
}

/** "2 days ago", "in 3 weeks". */
export function formatRelative(iso: string, locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;

  const diffMs = date.getTime() - Date.now();
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const divisions: [number, Intl.RelativeTimeFormatUnit][] = [
    [60_000, "second"],
    [3_600_000, "minute"],
    [86_400_000, "hour"],
    [604_800_000, "day"],
    [2_629_746_000, "week"],
    [31_556_952_000, "month"],
    [Infinity, "year"],
  ];

  let previous = 1000;
  for (const [limit, unit] of divisions) {
    if (Math.abs(diffMs) < limit) {
      return formatter.format(Math.round(diffMs / previous), unit);
    }
    previous = limit;
  }
  return formatter.format(Math.round(diffMs / 31_556_952_000), "year");
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  // Cut at a word boundary so the ellipsis doesn't land mid-word.
  const cut = value.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

/** Generate a URL-safe random string, used for OAuth nonces. */
export function randomNonce(bytes = 16): string {
  const array = new Uint8Array(bytes);
  crypto.getRandomValues(array);
  return Array.from(array, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Lower-case and strip accents, so a search for "cancun" finds "Cancún". */
export function normalizeSearch(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}
