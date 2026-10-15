/**
 * Map configuration and small helpers shared by every map in the product.
 *
 * Everything here works with **no API key**: the maps are drawn with Leaflet over
 * openly-licensed tiles. A hosted provider (MapTiler, Stadia, Mapbox...) can be
 * dropped in with environment variables, which is what you should do before
 * serious production traffic, because the free community tile servers have usage
 * policies (see the notes on each layer).
 */

import type { ActivityKind, PlaceCategory } from "@/types/api";

export type TileLayerId = "custom" | "mapbox" | "clear" | "standard" | "satellite";

export interface TileLayerDef {
  id: TileLayerId;
  label: string;
  url: string;
  attribution: string;
  maxZoom: number;
  subdomains?: string;
  /** Mapbox's raster API serves 512px tiles and needs this offset. */
  tileSize?: number;
  zoomOffset?: number;
}

const MAPBOX_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
const CUSTOM_URL = process.env.NEXT_PUBLIC_MAP_TILE_URL;
const CUSTOM_ATTRIBUTION = process.env.NEXT_PUBLIC_MAP_TILE_ATTRIBUTION;

const OSM = "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors";

/** Every layer available in this build, in the order the switcher shows them. */
export const TILE_LAYERS: TileLayerDef[] = [
  ...(CUSTOM_URL
    ? [
        {
          id: "custom" as const,
          label: "Map",
          url: CUSTOM_URL,
          attribution: CUSTOM_ATTRIBUTION ?? OSM,
          maxZoom: 19,
        },
      ]
    : []),
  ...(MAPBOX_TOKEN
    ? [
        {
          id: "mapbox" as const,
          label: "Mapbox",
          url: `/api/tiles/mapbox/{z}/{x}/{y}`, // Assuming proxy handles or we just keep it direct if we want
          attribution: "© <a href='https://www.mapbox.com/about/maps/'>Mapbox</a> " + OSM,
          maxZoom: 19,
          tileSize: 512,
          zoomOffset: -1,
        },
      ]
    : []),
  {
    id: "clear",
    label: "Clear",
    url: "/api/tiles/clear/{z}/{x}/{y}",
    attribution: `${OSM} © <a href='https://carto.com/attributions'>CARTO</a>`,
    maxZoom: 20,
    subdomains: "abcd",
  },
  {
    id: "standard",
    label: "Streets",
    url: "/api/tiles/streets/{z}/{x}/{y}",
    attribution: OSM,
    maxZoom: 19,
  },
  {
    id: "satellite",
    label: "Satellite",
    url: "/api/tiles/satellite/{z}/{x}/{y}",
    attribution: "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    maxZoom: 19,
  },
];

export const DEFAULT_TILE_LAYER: TileLayerId = TILE_LAYERS[0].id;

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------

export const ROUTE_COLOR = "#0f6f76";

/**
 * The route being compared *against*, drawn dashed beneath the proposed one.
 *
 * Deliberately desaturated: the baseline is context, and giving it a second
 * strong colour would read as two routes of equal standing.
 */
export const BASELINE_COLOR = "#94a3b8";

const KIND_STYLE: Record<string, { color: string; label: string }> = {
  visit: { color: ROUTE_COLOR, label: "Sight" },
  meal: { color: "#c2410c", label: "Meal" },
  check_in: { color: "#475569", label: "Check-in" },
  rest: { color: "#7c3aed", label: "Rest" },
  free_time: { color: "#7c3aed", label: "Free time" },
};

export function kindStyle(kind: ActivityKind | string) {
  return KIND_STYLE[kind] ?? KIND_STYLE.visit;
}

export type CategoryFamily =
  | "sights"
  | "nature"
  | "food"
  | "nightlife"
  | "shopping"
  | "other";

const FAMILY_STYLE: Record<CategoryFamily, { color: string; label: string }> = {
  sights: { color: "#0f6f76", label: "Sights & culture" },
  nature: { color: "#15803d", label: "Nature & beaches" },
  food: { color: "#c2410c", label: "Food & drink" },
  nightlife: { color: "#7c3aed", label: "Nightlife & shows" },
  shopping: { color: "#be185d", label: "Shopping" },
  other: { color: "#475569", label: "Other" },
};

const FAMILY_OF: Partial<Record<PlaceCategory, CategoryFamily>> = {
  landmark: "sights",
  attraction: "sights",
  museum: "sights",
  religious: "sights",
  viewpoint: "sights",
  park: "nature",
  nature: "nature",
  beach: "nature",
  restaurant: "food",
  cafe: "food",
  bar: "nightlife",
  nightlife: "nightlife",
  entertainment: "nightlife",
  shopping: "shopping",
};

export function categoryFamily(category: PlaceCategory): CategoryFamily {
  return FAMILY_OF[category] ?? "other";
}

export function familyStyle(family: CategoryFamily) {
  return FAMILY_STYLE[family];
}

export const FAMILY_ORDER: CategoryFamily[] = [
  "sights",
  "nature",
  "food",
  "nightlife",
  "shopping",
  "other",
];

// ---------------------------------------------------------------------------
// Geometry & markup
// ---------------------------------------------------------------------------

/** Initial compass bearing from a to b, in degrees clockwise from north. */
export function bearing(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLng = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const y = Math.sin(dLng) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** Popups are built from strings, so anything user-visible must be escaped. */
export function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char] ?? char,
  );
}

/** A numbered pin: the stop's position in the day, in a coloured disc. */
export function pinHtml(label: string | number, color: string): string {
  return `<span class="wp-pin" style="--pin:${color}">${escapeHtml(String(label))}</span>`;
}

/** A small chevron pointing along the route. */
export function arrowHtml(angle: number, color: string): string {
  return `<span class="wp-arrow" style="--pin:${color};transform:rotate(${angle.toFixed(0)}deg)"></span>`;
}

/** A plain coloured dot, for maps with many places where numbers would not mean anything. */
export function dotHtml(color: string): string {
  return `<span class="wp-dot" style="--pin:${color}"></span>`;
}
