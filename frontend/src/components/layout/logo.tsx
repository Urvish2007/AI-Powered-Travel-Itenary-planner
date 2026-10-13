import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * Wordmark.
 *
 * The mark is an inline SVG rather than an image file so it inherits the
 * current colour — the same component works on a white header, over a dark
 * hero photo, and in the footer without three separate assets.
 */
export function Logo({
  className,
  href = "/",
  showWordmark = true,
}: {
  className?: string;
  href?: string | null;
  showWordmark?: boolean;
}) {
  const content = (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span className="relative flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
        <svg viewBox="0 0 24 24" className="size-5" fill="none" aria-hidden>
          {/* A map pin whose centre is a compass needle. */}
          <path
            d="M12 21.5s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
          <path
            d="m14.6 7.9-1.4 4-4 1.4 1.4-4 4-1.4Z"
            fill="currentColor"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      {showWordmark && (
        <span className="font-display text-xl font-extrabold tracking-tight">
          Waypoint
        </span>
      )}
    </span>
  );

  if (!href) return content;

  return (
    <Link href={href} aria-label="Waypoint — home" className="shrink-0">
      {content}
    </Link>
  );
}
