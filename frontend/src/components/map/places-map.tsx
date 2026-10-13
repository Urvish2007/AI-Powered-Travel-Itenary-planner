"use client";

/**
 * A city's places on one map, coloured by what kind of place they are.
 *
 * Unlike the itinerary map there is no order here, so pins are dots rather than
 * numbers. Hover shows the name; click opens the details.
 */

import type * as Leaflet from "leaflet";
import { MapPin } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { MapFrame } from "@/components/map/map-frame";
import { useLeafletMap } from "@/components/map/use-leaflet-map";
import {
  FAMILY_ORDER,
  categoryFamily,
  dotHtml,
  escapeHtml,
  familyStyle,
} from "@/lib/map";
import { titleCase } from "@/lib/utils";
import type { Place } from "@/types/api";

export function PlacesMap({
  places,
  className,
  selectedId,
  onSelect,
  label = "Map of places in this city",
}: {
  places: Place[];
  className?: string;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  label?: string;
}) {
  const { containerRef, ctx, layerId, selectLayer, tilesFailed, setTilesFailed, retryTiles } =
    useLeafletMap();
  const bounds = useRef<Leaflet.LatLngBounds | null>(null);
  const markers = useRef(new Map<string, Leaflet.Marker>());

  // Read the latest callback from a ref so a parent re-render does not rebuild
  // every marker (and reset the viewport) just because it passed a new function.
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  });

  const located = useMemo(
    () => places.filter((place) => place.location?.coordinates?.length === 2),
    [places],
  );

  const families = useMemo(() => {
    const present = new Set(
      located.map((place) => categoryFamily(place.category)),
    );
    return FAMILY_ORDER.filter((family) => present.has(family));
  }, [located]);

  useEffect(() => {
    if (!ctx || located.length === 0) return;
    const { L, map } = ctx;
    const group = L.layerGroup().addTo(map);
    const registry = markers.current;
    registry.clear();
    const points: Leaflet.LatLngTuple[] = [];

    for (const place of located) {
      // GeoJSON order is [lng, lat].
      const latLng: Leaflet.LatLngTuple = [
        place.location.coordinates[1],
        place.location.coordinates[0],
      ];
      points.push(latLng);
      const style = familyStyle(categoryFamily(place.category));

      const marker = L.marker(latLng, {
        icon: L.divIcon({
          className: "",
          html: dotHtml(style.color),
          iconSize: [20, 20],
          iconAnchor: [10, 10],
          popupAnchor: [0, -10],
        }),
        title: place.name,
        riseOnHover: true,
      })
        .bindTooltip(escapeHtml(place.name), {
          direction: "top",
          offset: [0, -10],
        })
        .bindPopup(
          `<div class="wp-popup">
             <strong>${escapeHtml(place.name)}</strong>
             <span>${escapeHtml(titleCase(place.category))}${
               place.rating > 0 ? ` · ★ ${place.rating.toFixed(1)}` : ""
             }</span>
             <p>${escapeHtml(place.description)}</p>
           </div>`,
          { closeButton: false, maxWidth: 260, offset: [0, 2] },
        )
        .on("click", () => onSelectRef.current?.(place.id))
        .addTo(group);
      registry.set(place.id, marker);
    }

    bounds.current = L.latLngBounds(points);
    map.fitBounds(bounds.current, {
      padding: [48, 48],
      maxZoom: 15,
      animate: false,
    });

    // After fitting bounds, the container may still be at its initial size.
    // A deferred invalidateSize forces Leaflet to re-read the real dimensions
    // so every tile loads.
    requestAnimationFrame(() => {
      map.invalidateSize({ animate: false });
      if (bounds.current) {
        map.fitBounds(bounds.current, {
          padding: [48, 48],
          maxZoom: 15,
          animate: false,
        });
      }
    });

    return () => {
      group.remove();
      registry.clear();
    };
  }, [ctx, located]);

  useEffect(() => {
    if (!ctx || !selectedId) return;
    const marker = markers.current.get(selectedId);
    if (marker) {
      ctx.map.panTo(marker.getLatLng());
      marker.openPopup();
    }
  }, [ctx, selectedId, located]);

  const fit = useCallback(() => {
    if (ctx && bounds.current) {
      ctx.map.fitBounds(bounds.current, { padding: [48, 48], maxZoom: 15 });
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
      onFit={located.length > 1 ? fit : undefined}
      label={label}
      className={className}
    >
      {located.length === 0 ? (
        <div className="absolute inset-0 z-10 grid place-items-center bg-card/80 p-6 text-center backdrop-blur-sm">
          <div>
            <MapPin className="mx-auto size-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium">No places to show</p>
          </div>
        </div>
      ) : (
        <ul
          aria-label="Map key"
          className="absolute inset-x-3 bottom-3 z-10 flex flex-wrap gap-x-4 gap-y-1.5 rounded-xl border border-border bg-card/95 px-4 py-2.5 text-xs shadow-md backdrop-blur"
        >
          {families.map((family) => {
            const style = familyStyle(family);
            return (
              <li key={family} className="flex items-center gap-1.5">
                <span
                  className="size-2.5 rounded-full ring-2 ring-white"
                  style={{ background: style.color }}
                />
                {style.label}
              </li>
            );
          })}
        </ul>
      )}
    </MapFrame>
  );
}
