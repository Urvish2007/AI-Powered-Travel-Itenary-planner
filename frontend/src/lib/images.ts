/**
 * Curated photography.
 *
 * Every id below was checked against the Unsplash CDN and visually confirmed to
 * show what its key claims — a "colosseum" key really is the Colosseum. That
 * matters more than it sounds: a destination card showing the wrong city is the
 * fastest way to lose a visitor's trust.
 *
 * `photo()` builds a sized, cropped URL. Requesting the exact dimensions we
 * render keeps payloads small and avoids the layout shift that comes from
 * letting the browser scale a 4000px original.
 */

const CDN = "https://images.unsplash.com";

export type ImageKey = keyof typeof CATALOGUE;

/** Verified Unsplash photo ids, grouped by what they actually depict. */
export const CATALOGUE = {
  // --- Paris ---
  parisEiffelDusk: "photo-1502602898657-3e91760cbb34",
  parisEiffelDay: "photo-1511739001486-6bfe10ce785f",
  parisStreet: "photo-1522093007474-d86e9bf7ba6f",
  parisBridge: "photo-1499856871958-5b9627545d1a",
  parisEiffelBlue: "photo-1431274172761-fca41d930114",
  parisWinter: "photo-1549144511-f099e773c147",
  parisRooftops: "photo-1503917988258-f87a78e3c995",

  // --- Rome / Italy ---
  romeColosseumDusk: "photo-1552832230-c0197dd311b5",
  romeColosseumAerial: "photo-1555992828-ca4dbe41d294",
  cinqueTerre: "photo-1516483638261-f4dbaf036963",
  veniceCanal: "photo-1498307833015-e7b400441eb8",
  veniceRialto: "photo-1523906834658-6e24ef2386f9",

  // --- Tokyo / Japan ---
  tokyoShibuya: "photo-1542051841857-5f90071e7989",
  tokyoNeonStreet: "photo-1540959733332-eab4deabeeaf",
  tokyoTowerDusk: "photo-1536098561742-ca998e48cbcc",
  tokyoNightSigns: "photo-1503899036084-c55cdd92da26",
  tokyoBlossom: "photo-1524413840807-0c3cb6fa808d",
  japanStreet: "photo-1493976040374-85c8e12f0c0e",
  japanStorefront: "photo-1480796927426-f609979314bd",
  fujiPagoda: "photo-1526481280693-3bfa7568e0f3",
  fujiMisty: "photo-1528164344705-47542687000d",
  fujiSunset: "photo-1542640244-7e672d6cef4e",
  fujiAutumn: "photo-1570459027562-4a916cc6113f",

  // --- Other destinations ---
  londonAerial: "photo-1513635269975-59663e0ac1ad",
  londonTowerBridge: "photo-1533929736458-ca588d08c8be",
  santorini: "photo-1533105079780-92b9be482077",
  sanFrancisco: "photo-1534050359320-02900022671e",
  dubai: "photo-1518684079-3c830dcef090",
  seoulStreet: "photo-1517154421773-0529f29ea451",

  // --- Categories ---
  museum: "photo-1554907984-15263bfd63bd",
  restaurant: "photo-1414235077428-338989a2e8c0",
  food: "photo-1504674900247-0877df9cc836",
  cafe: "photo-1555396273-367ea4eb4db5",
  bar: "photo-1514933651103-005eec06c04b",
  hotel: "photo-1517840901100-8179e982acb7",
  resort: "photo-1566073771259-6a8506099945",
  beach: "photo-1507525428034-b723cf961d3e",
  nature: "photo-1470071459604-3b5ec3a7fe05",
  mountains: "photo-1469474968028-56623f02e42e",
  lake: "photo-1501785888041-af3ef285b470",
  boat: "photo-1476514525535-07fb3b4ae5f1",

  // --- Storytelling ---
  planningFlatLay: "photo-1488646953014-85cb44e25828",
  traveller: "photo-1527631746610-bca00a040d60",
  flight: "photo-1436491865332-7a61a109cc05",
} as const;

export interface PhotoOptions {
  width?: number;
  height?: number;
  /** 1–100. Lower is fine for decorative imagery. */
  quality?: number;
  /** Where to crop toward when the aspect ratio differs. */
  crop?: "entropy" | "faces" | "center" | "top";
}

/** Build a sized CDN URL for a catalogue entry. */
export function photo(key: ImageKey, options: PhotoOptions = {}): string {
  const { width = 1200, height, quality = 78, crop = "entropy" } = options;
  const params = new URLSearchParams({
    auto: "format",
    fit: "crop",
    q: String(quality),
    w: String(width),
    crop,
  });
  if (height) params.set("h", String(height));
  return `${CDN}/${CATALOGUE[key]}?${params.toString()}`;
}

/**
 * A tiny, heavily-blurred version of the same photo, used as `blurDataURL`.
 * Next/Image shows it while the full image streams in, so a card never flashes
 * from empty grey to photo.
 */
export function blurPhoto(key: ImageKey): string {
  return `${CDN}/${CATALOGUE[key]}?auto=format&fit=crop&w=24&q=20&blur=100`;
}

/** Ordered image sets for the hero rotator. */
export const HERO_IMAGES: ImageKey[] = [
  "parisEiffelDusk",
  "tokyoShibuya",
  "romeColosseumDusk",
  "santorini",
];

/**
 * Photography for a city we hold no data for yet.
 *
 * The fallback is deterministic rather than random, so the same city always
 * gets the same picture — a card that changes image between renders looks
 * broken.
 */
const CITY_IMAGES: Record<string, ImageKey> = {
  paris: "parisEiffelDusk",
  rome: "romeColosseumDusk",
  tokyo: "tokyoShibuya",
  london: "londonTowerBridge",
  venice: "veniceCanal",
  dubai: "dubai",
  seoul: "seoulStreet",
  "san francisco": "sanFrancisco",
  santorini: "santorini",
};

const GENERIC_CITY_IMAGES: ImageKey[] = [
  "traveller",
  "mountains",
  "beach",
  "lake",
  "nature",
];

/** True when we hold a real photograph of this city (not a generic stand-in). */
export function hasCityPhoto(city: string): boolean {
  return city.trim().toLowerCase() in CITY_IMAGES;
}

/**
 * A deterministic, dark-enough gradient for cities without a photograph. Showing
 * a lake or a beach for Chicago would be misleading; a plain colour is honest.
 */
export function cityGradient(city: string): string {
  let hash = 0;
  for (const char of city) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${hash} 52% 34%), hsl(${(hash + 42) % 360} 58% 22%))`;
}

export function cityImage(city: string): ImageKey {
  const key = CITY_IMAGES[city.trim().toLowerCase()];
  if (key) return key;

  // Deterministic pick: sum the character codes so one city keeps one image.
  let hash = 0;
  for (const char of city) hash = (hash + char.charCodeAt(0)) % 997;
  return GENERIC_CITY_IMAGES[hash % GENERIC_CITY_IMAGES.length];
}

/** Photography for a place category, when the place has no image of its own. */
const CATEGORY_IMAGES: Record<string, ImageKey> = {
  museum: "museum",
  restaurant: "restaurant",
  cafe: "cafe",
  bar: "bar",
  nightlife: "bar",
  hotel: "hotel",
  beach: "beach",
  park: "nature",
  nature: "mountains",
  viewpoint: "santorini",
  landmark: "parisEiffelDay",
  attraction: "parisEiffelDay",
  religious: "japanStreet",
  shopping: "japanStorefront",
  entertainment: "tokyoNightSigns",
  transport: "flight",
  other: "traveller",
};

export function categoryImage(category: string): ImageKey {
  return CATEGORY_IMAGES[category?.toLowerCase()] ?? "traveller";
}

/**
 * The image to show for a place: its own photo when the data has one, a
 * category-appropriate stand-in otherwise. Returning a plain URL either way
 * means callers never branch on which it was.
 */
export function placeImage(
  place: { image_url?: string | null; category?: string; city?: string },
  options: PhotoOptions = {},
): string {
  if (place.image_url) return place.image_url;
  return photo(categoryImage(place.category ?? "other"), options);
}
