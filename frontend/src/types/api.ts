/**
 * Types mirroring the backend's OpenAPI schema.
 *
 * Hand-written rather than generated, because the generated output for this API
 * is noisy and these are the only shapes the UI actually consumes. They are
 * kept deliberately close to the server's field names so a response can be used
 * without a translation layer.
 */

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface PageMeta {
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export interface ApiMessage {
  message: string;
  details?: Record<string, unknown>;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

export interface GeoPoint {
  type: "Point";
  /** GeoJSON order: [longitude, latitude]. */
  coordinates: [number, number];
}

// ---------------------------------------------------------------------------
// Enums (string unions, matching the server's StrEnum values)
// ---------------------------------------------------------------------------

export type TravelPace = "relaxed" | "moderate" | "packed";

export type BudgetLevel =
  | "shoestring"
  | "budget"
  | "moderate"
  | "comfortable"
  | "luxury";

export type TransportMode = "walking" | "driving" | "cycling" | "transit";

export type InterestTag =
  | "history"
  | "art"
  | "food"
  | "nature"
  | "adventure"
  | "nightlife"
  | "shopping"
  | "relaxation"
  | "family"
  | "photography"
  | "architecture"
  | "culture"
  | "sports"
  | "music";

export type AccessibilityNeed =
  | "wheelchair"
  | "step_free"
  | "low_walking"
  | "hearing"
  | "visual";

export type PlaceCategory =
  | "attraction"
  | "museum"
  | "park"
  | "restaurant"
  | "cafe"
  | "bar"
  | "shopping"
  | "landmark"
  | "beach"
  | "viewpoint"
  | "religious"
  | "nightlife"
  | "entertainment"
  | "nature"
  | "hotel"
  | "transport"
  | "other";

export type TripStatus = "draft" | "planning" | "ready" | "archived";

export type ItineraryStatus =
  | "pending"
  | "generating"
  | "optimizing"
  | "completed"
  | "failed";

export type ActivityKind =
  | "visit"
  | "meal"
  | "travel"
  | "rest"
  | "check_in"
  | "free_time";

export type AuthProviderName = "password" | "google" | "apple";

// ---------------------------------------------------------------------------
// Auth & user
// ---------------------------------------------------------------------------

export interface TravelPreferences {
  interests: InterestTag[];
  pace: TravelPace;
  budget_level: BudgetLevel;
  preferred_transport: TransportMode;
  accessibility_needs: AccessibilityNeed[];
  dietary_restrictions: string[];
  avoid_categories: string[];
  day_start_time: string;
  day_end_time: string;
  max_daily_walking_km: number;
  language: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "user" | "admin";
  is_active: boolean;
  is_verified: boolean;
  home_city: string | null;
  avatar_url: string | null;
  preferences: TravelPreferences;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthResponse {
  user: User;
  tokens: TokenPair;
}

export interface AuthMethods {
  password: boolean;
  google: boolean;
  apple: boolean;
}

export interface LinkedProviders {
  providers: AuthProviderName[];
  has_password: boolean;
}

// ---------------------------------------------------------------------------
// Places
// ---------------------------------------------------------------------------

export interface PlaceCost {
  amount: number;
  currency: string;
  is_free: boolean;
}

export interface OpeningHours {
  /** Monday is 0, Sunday is 6. */
  day_of_week: number;
  opens: string | null;
  closes: string | null;
  closed: boolean;
}

export interface Place {
  id: string;
  name: string;
  slug: string;
  description: string;
  category: PlaceCategory;
  tags: InterestTag[];
  location: GeoPoint;
  address: string | null;
  city: string;
  country: string;
  timezone: string;
  rating: number;
  review_count: number;
  popularity: number;
  average_visit_minutes: number;
  cost: PlaceCost;
  opening_hours: OpeningHours[];
  wheelchair_accessible: boolean | null;
  family_friendly: boolean;
  image_url: string | null;
  website: string | null;
  is_active: boolean;
  created_at: string;
  /** Present on nearby searches. */
  distance_km?: number | null;
  /** Present on text and semantic searches. */
  search_score?: number | null;
}

export interface CitySummary {
  city: string;
  country: string;
  place_count: number;
  average_rating: number;
  latitude: number | null;
  longitude: number | null;
}

// ---------------------------------------------------------------------------
// Trips
// ---------------------------------------------------------------------------

export interface Destination {
  city: string;
  country: string;
  location: GeoPoint;
  timezone: string;
}

export interface PartyComposition {
  adults: number;
  children: number;
  infants: number;
}

export interface TripConstraints {
  pace: TravelPace;
  budget_level: BudgetLevel;
  budget_total: number | null;
  currency: string;
  interests: InterestTag[];
  preferred_transport: TransportMode;
  accessibility_needs: AccessibilityNeed[];
  dietary_restrictions: string[];
  day_start_time: string;
  day_end_time: string;
  max_daily_walking_km: number;
  must_include_place_ids: string[];
  must_avoid_place_ids: string[];
  notes: string | null;
  /**
   * Which algorithm plans this trip's routes.
   *
   * `null` means no choice has been saved — the case for every trip created
   * before the field existed — and the server's OPTIMIZER_ALGORITHM decides.
   */
  optimizer_algorithm: OptimizerAlgorithm | null;
}

export interface Trip {
  id: string;
  user_id: string;
  title: string;
  destination: Destination;
  start_date: string;
  end_date: string;
  duration_days: number;
  party: PartyComposition;
  constraints: TripConstraints;
  status: TripStatus;
  active_itinerary_id: string | null;
  is_public: boolean;
  share_token: string | null;
  created_at: string;
  updated_at: string;
}

export interface DestinationInput {
  city: string;
  country: string;
  latitude: number;
  longitude: number;
  timezone?: string;
}

export interface TripCreateInput {
  title?: string;
  destination: DestinationInput;
  start_date: string;
  end_date: string;
  party?: Partial<PartyComposition>;
  constraints?: Partial<TripConstraints>;
}

// ---------------------------------------------------------------------------
// Itineraries
// ---------------------------------------------------------------------------

export interface TravelLeg {
  mode: TransportMode;
  distance_km: number;
  duration_minutes: number;
  polyline: string | null;
}

export interface Activity {
  place_id: string | null;
  title: string;
  kind: ActivityKind;
  description: string;
  start_time: string;
  end_time: string;
  duration_minutes: number;
  location: GeoPoint | null;
  address: string | null;
  estimated_cost: number;
  currency: string;
  rationale: string;
  booking_required: boolean;
  booking_url: string | null;
  travel_from_previous: TravelLeg | null;
  order: number;
}

export interface ItineraryDay {
  day_number: number;
  day_date: string;
  title: string;
  summary: string;
  activities: Activity[];
  total_distance_km: number;
  total_walking_km: number;
  estimated_cost: number;
  activity_count: number;
}

export interface OptimizationReport {
  ran: boolean;
  solver_status: string;
  objective_value: number | null;
  duration_seconds: number;
  distance_before_km: number | null;
  distance_after_km: number | null;
  places_scheduled: number;
  places_dropped: number;
  dropped_reasons: Record<string, string>;
  distance_saved_km: number | null;
}

export interface GenerationMeta {
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  generated_by: string;
  warnings: string[];
}

export interface Itinerary {
  id: string;
  trip_id: string;
  user_id: string;
  version: number;
  status: ItineraryStatus;
  title: string;
  summary: string;
  days: ItineraryDay[];
  total_estimated_cost: number;
  currency: string;
  total_distance_km: number;
  optimization: OptimizationReport;
  generation: GenerationMeta;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ItinerarySummary {
  id: string;
  trip_id: string;
  version: number;
  status: ItineraryStatus;
  title: string;
  total_estimated_cost: number;
  currency: string;
  total_distance_km: number;
  day_count: number;
  created_at: string;
}

export interface ItineraryStats {
  day_count: number;
  activity_count: number;
  activities_per_day: number;
  by_kind: Record<string, number>;
  total_distance_km: number;
  total_estimated_cost: number;
  currency: string;
  busiest_day: number | null;
  optimization: OptimizationReport;
}

// ---------------------------------------------------------------------------
// AI
// ---------------------------------------------------------------------------

export interface TripIntent {
  interests: InterestTag[];
  pace: TravelPace;
  budget_level: BudgetLevel;
  keywords: string[];
  notes: string;
}

export interface AIRunStep {
  step: string;
  elapsed_ms: number;
  [key: string]: unknown;
}

export interface AIRun {
  id: string;
  trip_id: string | null;
  itinerary_id: string | null;
  kind: string;
  provider: string;
  model: string;
  status: string;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  steps: AIRunStep[];
  error: string | null;
  created_at: string;
}

export interface UserStats {
  total: number;
  by_status: Record<string, number>;
  itineraries: number;
}


// --- Route optimisation --------------------------------------------------

export type OptimizerAlgorithm =
  | "auto"
  | "ortools"
  | "nearest_neighbor"
  | "cheapest_insertion"
  | "local_search"
  | "simulated_annealing"
  | "genetic";

export interface AlgorithmInfo {
  key: OptimizerAlgorithm;
  label: string;
  speed: string;
  quality: string;
  description: string;
}

export interface OptimizedStop {
  place_id: string;
  name: string;
  day_number: number;
  order: number;
  arrival_time: string;
  departure_time: string;
  travel_km: number;
  travel_minutes: number;
  mode: string;
}

export interface AlgorithmRun {
  algorithm: string;
  label: string;
  solver_status: string;
  scheduled_count: number;
  dropped_count: number;
  total_distance_km: number;
  travel_minutes: number;
  /** Plan cost in minutes (travel + visits + penalties). Lower is better. */
  objective: number;
  /** How much worse than the best plan, in percent. */
  gap_percent: number;
  duration_seconds: number;
  is_best: boolean;
  stops: OptimizedStop[];
  dropped_place_ids: string[];
}

export interface ApplyRouteInput {
  /** The day's routable places, in the order to apply. */
  place_ids: string[];
  algorithm: OptimizerAlgorithm;
  /**
   * The itinerary's `updated_at` as of the comparison.
   *
   * Echoed back verbatim — the server compares it with the stored value and
   * returns 409 if the day changed in the meantime.
   */
  expected_updated_at: string;
}

export interface CompareRouteResponse {
  runs: AlgorithmRun[];
  best_algorithm: string;
  fastest_algorithm: string;
}

export interface CompareRouteInput {
  place_ids: string[];
  start_latitude: number;
  start_longitude: number;
  day_start_time?: string;
  day_end_time?: string;
  days?: number;
  transport_mode?: TransportMode;
  max_places_per_day?: number;
  /** ISO date of the day being planned, so opening hours use the right weekday. */
  start_date?: string;
  algorithms?: OptimizerAlgorithm[];
  time_limit_seconds?: number;
}
