"use client";

import { Crosshair, WifiOff } from "lucide-react";
import type { ReactNode, RefObject } from "react";

import { cn } from "@/lib/utils";
import { TILE_LAYERS, type TileLayerId } from "@/lib/map";

/**
 * The chrome around a map: the style switcher, a "fit everything" button and a
 * notice when tiles cannot load. The map itself is drawn into the inner div.
 */
export function MapFrame({
  containerRef,
  layerId,
  onLayerChange,
  tilesFailed,
  onRetry,
  onDismiss,
  onFit,
  label,
  className,
  children,
}: {
  containerRef: RefObject<HTMLDivElement | null>;
  layerId: TileLayerId;
  onLayerChange: (id: TileLayerId) => void;
  tilesFailed: boolean;
  onRetry?: () => void;
  onDismiss?: () => void;
  onFit?: () => void;
  label: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "relative h-full min-h-72 w-full overflow-hidden rounded-2xl border border-border bg-muted",
        className,
      )}
    >
      <div
        ref={containerRef}
        className="absolute inset-0 z-0"
        role="application"
        aria-label={label}
      />

      <div
        role="group"
        aria-label="Map style"
        className="absolute left-3 top-3 z-10 flex overflow-hidden rounded-lg border border-border bg-card/95 text-xs font-medium shadow-md backdrop-blur"
      >
        {TILE_LAYERS.map((layer) => (
          <button
            key={layer.id}
            type="button"
            onClick={() => onLayerChange(layer.id)}
            aria-pressed={layerId === layer.id}
            className={cn(
              "px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              layerId === layer.id
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {layer.label}
          </button>
        ))}
      </div>

      {onFit && (
        <button
          type="button"
          onClick={onFit}
          aria-label="Fit the whole route in view"
          title="Fit everything in view"
          className="absolute right-3 top-[5.5rem] z-10 grid size-[30px] place-items-center rounded-md border border-border bg-card/95 text-foreground shadow-md backdrop-blur transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Crosshair className="size-4" />
        </button>
      )}

      {tilesFailed && (
        <div
          role="status"
          aria-live="polite"
          className="absolute inset-x-3 top-14 z-10 flex flex-col items-start gap-3 rounded-lg border border-warning/30 bg-card/95 px-4 py-3 text-xs shadow-md backdrop-blur pointer-events-none"
        >
          <div className="flex items-center gap-2">
            <WifiOff className="size-4 shrink-0 text-warning" />
            <span>
              The map background could not load. Your route is still shown.
            </span>
          </div>
          <div className="flex flex-wrap gap-2 pointer-events-auto">
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="rounded bg-primary px-2.5 py-1.5 text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Retry
              </button>
            )}
            <button
              type="button"
              onClick={() => onLayerChange("clear")}
              className="rounded border border-border bg-background px-2.5 py-1.5 transition-colors hover:bg-muted"
            >
              Use Clear style
            </button>
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                className="rounded border border-border bg-background px-2.5 py-1.5 transition-colors hover:bg-muted"
              >
                Dismiss
              </button>
            )}
          </div>
        </div>
      )}

      {children}
    </div>
  );
}
