"use client";

// leaflet.css is imported globally in globals.css (after Tailwind) so
// Preflight overrides are correctly handled. Do NOT re-import it here.

import type * as Leaflet from "leaflet";
import { useCallback, useEffect, useRef, useState } from "react";

import { DEFAULT_TILE_LAYER, TILE_LAYERS, type TileLayerId } from "@/lib/map";

/**
 * Fix the well-known Leaflet + Webpack/Turbopack icon issue.
 *
 * Leaflet's `Icon.Default` resolves marker image URLs through a
 * `_getIconUrl` method that inspects CSS `background-image` values.
 * Bundlers strip or rewrite those URLs, leaving the default icon broken
 * (missing marker images, console 404s). Deleting the accessor and setting
 * the URLs explicitly from the npm package makes it work in any bundler.
 */
let iconFixApplied = false;
async function fixDefaultIcon(L: typeof Leaflet) {
  if (iconFixApplied) return;
  iconFixApplied = true;

  // Import the marker images so the bundler processes them.
  const iconRetinaUrl = (await import("leaflet/dist/images/marker-icon-2x.png")).default;
  const iconUrl = (await import("leaflet/dist/images/marker-icon.png")).default;
  const shadowUrl = (await import("leaflet/dist/images/marker-shadow.png")).default;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (L.Icon.Default.prototype as any)._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: typeof iconRetinaUrl === "string" ? iconRetinaUrl : iconRetinaUrl?.src ?? "",
    iconUrl: typeof iconUrl === "string" ? iconUrl : iconUrl?.src ?? "",
    shadowUrl: typeof shadowUrl === "string" ? shadowUrl : shadowUrl?.src ?? "",
  });
}

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
  const [retryCount, setRetryCount] = useState(0);

  const retryTiles = useCallback(() => {
    setTilesFailed(false);
    setRetryCount(r => r + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let instance: Leaflet.Map | null = null;
    let observer: ResizeObserver | null = null;
    let delayedInvalidation: ReturnType<typeof setTimeout>;

    (async () => {
      const L = await loadLeaflet();
      const element = containerRef.current;
      if (cancelled || !element) return;

      await fixDefaultIcon(L);

      if (cancelled) return;

      instance = L.map(element, {
        zoomControl: false,
        worldCopyJump: true,
        minZoom: 2,
        scrollWheelZoom: false,
      }).setView([20, 0], 2);
      L.control.zoom({ position: "bottomright" }).addTo(instance);
      const wheel = instance.scrollWheelZoom;
      instance.on("focus", () => wheel.enable());
      instance.on("blur", () => wheel.disable());

      observer = new ResizeObserver(() => instance?.invalidateSize());
      observer.observe(element);

      requestAnimationFrame(() => {
        instance?.invalidateSize({ animate: false });
      });

      // A second delayed invalidation handles containers that start at 0×0
      // (e.g. sticky sidebars, dialog mounts, or tabs that haven't painted yet).
      // 200ms is enough for CSS transitions and layout to settle.
      delayedInvalidation = setTimeout(() => {
        instance?.invalidateSize({ animate: false });
      }, 200);

      setCtx({ L, map: instance });
    })();

    return () => {
      cancelled = true;
      clearTimeout(delayedInvalidation);
      observer?.disconnect();
      instance?.remove();
      instance = null;
    };
  }, []);

  useEffect(() => {
    if (!ctx) return;
    let cancelled = false;
    const definition =
      TILE_LAYERS.find((layer) => layer.id === layerId) ?? TILE_LAYERS[0];

    const layer = ctx.L.tileLayer(definition.url, {
      attribution: definition.attribution,
      maxZoom: definition.maxZoom,
      subdomains: definition.subdomains ?? "abc",
      tileSize: definition.tileSize ?? 256,
      zoomOffset: definition.zoomOffset ?? 0,
    });

    let ok = 0;
    let fail = 0;
    const t0 = Date.now();
    
    layer.on("tileload", () => {
      ok += 1;
      setTilesFailed(false);
    });
    layer.on("tileerror", () => {
      if (cancelled) return;
      fail += 1;
      if (ok === 0 && fail >= 4 && Date.now() - t0 < 8000) {
        setTilesFailed(true);
      }
    });

    const onOnline = () => {
      setTilesFailed(false);
    };
    window.addEventListener("online", onOnline);

    layer.addTo(ctx.map);

    requestAnimationFrame(() => ctx.map.invalidateSize({ animate: false }));

    return () => {
      cancelled = true;
      layer.remove();
      window.removeEventListener("online", onOnline);
    };
  }, [ctx, layerId, retryCount]);

  const selectLayer = useCallback((id: TileLayerId) => {
    setLayerId(id);
    setTilesFailed(false);
  }, []);

  return { containerRef, ctx, layerId, selectLayer, tilesFailed, setTilesFailed, retryTiles };
}
