"use client";

import "leaflet/dist/leaflet.css";

import type * as Leaflet from "leaflet";
import { useCallback, useEffect, useRef, useState } from "react";

import { DEFAULT_TILE_LAYER, TILE_LAYERS, type TileLayerId } from "@/lib/map";

export interface LeafletContext {
  L: typeof Leaflet;
  map: Leaflet.Map;
}

/** Leaflet touches `window` on import, so it is only ever loaded in the browser. */
async function loadLeaflet(): Promise<typeof Leaflet> {
  const mod = await import("leaflet");
  return ((mod as unknown as { default?: typeof Leaflet }).default ??
    mod) as typeof Leaflet;
}

/**
 * Creates one Leaflet map in the returned container and keeps its tile layer in
 * sync with the chosen style.
 *
 * Callers draw their own markers and lines into the returned context. The map is
 * created once and survives data changes, so panning and zooming are not reset
 * every time the data refreshes.
 */
export function useLeafletMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [ctx, setCtx] = useState<LeafletContext | null>(null);
  const [layerId, setLayerId] = useState<TileLayerId>(DEFAULT_TILE_LAYER);
  const [tilesFailed, setTilesFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let instance: Leaflet.Map | null = null;
    let observer: ResizeObserver | null = null;

    (async () => {
      const L = await loadLeaflet();
      const element = containerRef.current;
      if (cancelled || !element) return;

      instance = L.map(element, {
        zoomControl: false,
        worldCopyJump: true,
        minZoom: 2,
        // A map inside a scrolling page must not swallow the scroll wheel; zoom
        // by wheel only once the map has been clicked (see focus handlers below).
        scrollWheelZoom: false,
      }).setView([20, 0], 2);
      L.control.zoom({ position: "topright" }).addTo(instance);
      const wheel = instance.scrollWheelZoom;
      instance.on("focus", () => wheel.enable());
      instance.on("blur", () => wheel.disable());

      // The panel can resize (sticky sidebar, dialog opening, window resize);
      // without this Leaflet keeps the old size and leaves grey tiles.
      observer = new ResizeObserver(() => instance?.invalidateSize());
      observer.observe(element);

      setCtx({ L, map: instance });
    })();

    return () => {
      cancelled = true;
      observer?.disconnect();
      instance?.remove();
      instance = null;
    };
  }, []);

  useEffect(() => {
    if (!ctx) return;
    const definition =
      TILE_LAYERS.find((layer) => layer.id === layerId) ?? TILE_LAYERS[0];

    const layer = ctx.L.tileLayer(definition.url, {
      attribution: definition.attribution,
      maxZoom: definition.maxZoom,
      subdomains: definition.subdomains ?? "abc",
      tileSize: definition.tileSize ?? 256,
      zoomOffset: definition.zoomOffset ?? 0,
    });

    // If the very first tiles all fail (offline, blocked, rate-limited) say so,
    // rather than leaving a blank grey map that looks broken.
    let loaded = 0;
    let errors = 0;
    layer.on("tileload", () => {
      loaded += 1;
    });
    layer.on("tileerror", () => {
      errors += 1;
      if (loaded === 0 && errors >= 4) setTilesFailed(true);
    });

    layer.addTo(ctx.map);
    return () => {
      layer.remove();
    };
  }, [ctx, layerId]);

  const selectLayer = useCallback((id: TileLayerId) => {
    setLayerId(id);
    setTilesFailed(false);
  }, []);

  return { containerRef, ctx, layerId, selectLayer, tilesFailed };
}
