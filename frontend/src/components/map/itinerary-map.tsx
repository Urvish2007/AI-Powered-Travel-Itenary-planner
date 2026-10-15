"use client";

/**
 * The day's route, on a real map.
 *
 * Needs no API key: tiles come from openly-licensed sources (see `lib/map.ts`).
 * Each stop is a numbered pin in visiting order, joined by a white-edged line
 * with direction arrows so the order and the direction of travel read at a glance.
 */

import type * as Leaflet from "leaflet";
import { MapPin, Navigation } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { MapFrame } from "@/components/map/map-frame";
import { useLeafletMap } from "@/components/map/use-leaflet-map";
import {
  BASELINE_COLOR,
  ROUTE_COLOR,
  arrowHtml,
  bearing,
  escapeHtml,
  kindStyle,
  pinHtml,
} from "@/lib/map";
import { formatDistance, formatTime } from "@/lib/utils";
import type { Activity } from "@/types/api";

export interface MapStop {
  title: string;
  latitude: number;
  longitude: number;
  startTime: string;
  order: number;
  kind: string;
}

/** Pull the stops that actually have coordinates out of a day's activities. */
export function activitiesToStops(activities: Activity[]): MapStop[] {
  return activities
    .filter((activity) => activity.location != null)
    .map((activity, index) => ({
      title: activity.title,
      // GeoJSON is [lng, lat]; getting this backwards drops every pin in the
      // wrong hemisphere.
      longitude: activity.location!.coordinates[0],
      latitude: activity.location!.coordinates[1],
      startTime: activity.start_time,
      order: index + 1,
      kind: activity.kind,
    }));
}

export function ItineraryMap({
  stops: incoming,
  className,
  distanceKm,
  routeColor = ROUTE_COLOR,
  label = "Map of the day's route",
  baseline: incomingBaseline,
  baselineLabel = "current route",
}: {
  stops: MapStop[];
  className?: string;
  distanceKm?: number;
  routeColor?: string;
  label?: string;
  /**
   * An existing route to show dashed underneath, for contrast. Used when
   * previewing a proposed reordering against the one already saved.
   */
  baseline?: MapStop[];
  baselineLabel?: string;
}) {
  const { containerRef, ctx, layerId, selectLayer, tilesFailed, setTilesFailed, retryTiles } =
    useLeafletMap();
  const bounds = useRef<Leaflet.LatLngBounds | null>(null);

  // Parents usually rebuild the stops array on every render. Keying on its
  // content keeps the map from redrawing (and losing the user's pan and zoom)
  // when nothing about the route has actually changed.
  const key = JSON.stringify(incoming);
  const stops = useMemo(() => JSON.parse(key) as MapStop[], [key]);

  const baselineKey = JSON.stringify(incomingBaseline ?? []);
  const baseline = useMemo(
    () => JSON.parse(baselineKey) as MapStop[],
    [baselineKey],
  );

  useEffect(() => {
    if (!ctx || stops.length === 0) return;
    const { L, map } = ctx;
    const group = L.layerGroup().addTo(map);
    const points = stops.map(
      (stop) => [stop.latitude, stop.longitude] as Leaflet.LatLngTuple,
    );
    const baselinePoints = baseline.map(
      (stop) => [stop.latitude, stop.longitude] as Leaflet.LatLngTuple,
    );

    // Drawn first so the proposed route layers over it, and dashed so the two
    // are distinguishable without relying on colour alone.
    if (baselinePoints.length > 1) {
      L.polyline(baselinePoints, {
        color: BASELINE_COLOR,
        weight: 3,
        opacity: 0.9,
        dashArray: "6 7",
        lineCap: "round",
        lineJoin: "round",
        interactive: false,
      }).addTo(group);
    }

    if (stops.length > 1) {
      // A white casing under the coloured line keeps the route legible over
      // busy streets and satellite imagery alike.
      const line = {
        lineCap: "round" as const,
        lineJoin: "round" as const,
        interactive: false,
      };
      L.polyline(points, {
        color: "#ffffff",
        weight: 9,
        opacity: 0.95,
        ...line,
      }).addTo(group);
      L.polyline(points, {
        color: routeColor,
        weight: 5,
        opacity: 1,
        ...line,
      }).addTo(group);

      stops.slice(0, -1).forEach((from, index) => {
        const to = stops[index + 1];
        if (from.latitude === to.latitude && from.longitude === to.longitude) {
          return;
        }
        L.marker(
          [
            (from.latitude + to.latitude) / 2,
            (from.longitude + to.longitude) / 2,
          ],
          {
            icon: L.divIcon({
              className: "",
              html: arrowHtml(bearing(from, to), routeColor),
              iconSize: [16, 16],
              iconAnchor: [8, 8],
            }),
            interactive: false,
            keyboard: false,
          },
        ).addTo(group);
      });
    }

    for (const stop of stops) {
      const style = kindStyle(stop.kind);
      L.marker([stop.latitude, stop.longitude], {
        icon: L.divIcon({
          className: "",
          html: pinHtml(stop.order, style.color),
          iconSize: [32, 32],
          iconAnchor: [16, 16],
          popupAnchor: [0, -18],
        }),
        title: `${stop.order}. ${stop.title}`,
        riseOnHover: true,
      })
        .bindPopup(
          `<div class="wp-popup">
             <strong>${escapeHtml(stop.title)}</strong>
             <span>${escapeHtml(formatTime(stop.startTime))} · ${escapeHtml(style.label)}</span>
           </div>`,
          { closeButton: false, offset: [0, 2] },
        )
        .addTo(group);
    }

    // The baseline is part of what the user is comparing, so it has to fit in
    // view too — otherwise half the contrast is off-screen.
    bounds.current = L.latLngBounds([...points, ...baselinePoints]);
    map.fitBounds(bounds.current, {
      padding: [56, 56],
      maxZoom: 16,
      animate: false,
    });

    // After fitting bounds, the container may still be at its initial size
    // (e.g. the first render of a sticky sidebar). A deferred invalidateSize
    // forces Leaflet to re-read the real dimensions so every tile loads.
    requestAnimationFrame(() => {
      map.invalidateSize({ animate: false });
      if (bounds.current) {
        map.fitBounds(bounds.current, {
          padding: [56, 56],
          maxZoom: 16,
          animate: false,
        });
      }
    });

    return () => {
      group.remove();
    };
  }, [ctx, stops, baseline, routeColor]);

  const fit = useCallback(() => {
    if (ctx && bounds.current) {
      ctx.map.fitBounds(bounds.current, { padding: [56, 56], maxZoom: 16 });
    }
  }, [ctx]);

  return (
    <MapFrame
      containerRef={containerRef}
      layerId={layerId}
      onLayerChange={selectLayer}
      tilesFailed={tilesFailed}
      onRetry={retryTiles}
      onDismiss={() => setTilesFailed(false)}
      onFit={stops.length > 1 ? fit : undefined}
      label={label}
      className={className}
    >
      {stops.length === 0 ? (
        <div className="absolute inset-0 z-10 grid place-items-center bg-card/80 p-6 text-center backdrop-blur-sm">
          <div>
            <MapPin className="mx-auto size-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium">Nothing to map yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Stops with a location appear here in visiting order.
            </p>
          </div>
        </div>
      ) : (
        <div className="absolute inset-x-3 bottom-3 z-10 flex items-center gap-2 rounded-xl border border-border bg-card/95 px-4 py-2.5 text-sm font-medium shadow-md backdrop-blur">
          <Navigation className="size-4 shrink-0 text-primary" />
          <span>{stops.length} stops in order</span>
          {distanceKm != null && distanceKm > 0 && (
            <span className="text-muted-foreground">
              · {formatDistance(distanceKm)}
            </span>
          )}
          {baseline.length > 1 && (
            <span className="flex items-center gap-1.5 text-muted-foreground">
              ·
              <svg width="18" height="4" aria-hidden className="shrink-0">
                <line
                  x1="0"
                  y1="2"
                  x2="18"
                  y2="2"
                  stroke={BASELINE_COLOR}
                  strokeWidth="3"
                  strokeDasharray="5 5"
                  strokeLinecap="round"
                />
              </svg>
              {baselineLabel}
            </span>
          )}
        </div>
      )}

      {/* The map is a picture to a screen reader; this is the same information. */}
      <ol className="sr-only">
        {stops.map((stop) => (
          <li key={stop.order}>
            {stop.order}. {stop.title}, {formatTime(stop.startTime)}
          </li>
        ))}
      </ol>
    </MapFrame>
  );
}
