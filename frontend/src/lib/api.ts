/**
 * Typed client for the itinerary API.
 *
 * Two things here are worth knowing about:
 *
 * **Token refresh is transparent and single-flight.** When a request comes back
 * 401, the client refreshes once and replays the original request. If five
 * requests fail at the same moment they all await the *same* refresh promise,
 * because the backend rotates refresh tokens single-use — five parallel
 * refreshes would invalidate each other and sign the user out.
 *
 * **Errors arrive as one shape.** The backend always answers failures with
 * `{ error: { code, message, details } }`, so `ApiError` carries a message that
 * is safe to show a user directly rather than a generic "something went wrong".
 */

import type {
  AIRun,
  AlgorithmInfo,
  ApiErrorBody,
  ApiMessage,
  AuthMethods,
  AuthResponse,
  CitySummary,
  CompareRouteInput,
  CompareRouteResponse,
  Itinerary,
  ItineraryStats,
  ItinerarySummary,
  LinkedProviders,
  Page,
  Place,
  PlaceCategory,
  TokenPair,
  Trip,
  TripCreateInput,
  TripIntent,
  User,
  UserStats,
} from "@/types/api";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

const API_V1 = `${API_BASE}/api/v1`;

const ACCESS_KEY = "itinerary.access_token";
const REFRESH_KEY = "itinerary.refresh_token";

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

/**
 * Tokens live in localStorage so a refresh of the page keeps the session.
 *
 * The honest trade-off: localStorage is readable by any script on the origin,
 * so an XSS bug becomes a token theft. The alternative — httpOnly cookies — needs
 * the API and the site to share a domain, which this split deployment does not.
 * Mitigation is therefore to keep access tokens short-lived (one hour) and to
 * rotate refresh tokens on every use, both of which the backend already does.
 */
export const tokenStore = {
  get access(): string | null {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(ACCESS_KEY);
    } catch {
      return null;
    }
  },
  get refresh(): string | null {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(REFRESH_KEY);
    } catch {
      return null;
    }
  },
  set(tokens: TokenPair) {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
      window.localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
      window.dispatchEvent(new Event("auth:changed"));
    } catch {
      /* Private browsing can block writes; the session simply won't persist. */
    }
  },
  clear() {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.removeItem(ACCESS_KEY);
      window.localStorage.removeItem(REFRESH_KEY);
      window.dispatchEvent(new Event("auth:changed"));
    } catch {
      /* ignore */
    }
  },
};

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** True when retrying could plausibly succeed. */
  get isRetryable(): boolean {
    return this.status >= 500 || this.status === 429;
  }

  get isAuth(): boolean {
    return this.status === 401;
  }
}

export class NetworkError extends Error {
  constructor(message = "Could not reach the server. Check your connection.") {
    super(message);
    this.name = "NetworkError";
  }
}

// ---------------------------------------------------------------------------
// Request plumbing
// ---------------------------------------------------------------------------

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Attach the access token. Defaults to true. */
  auth?: boolean;
  /** Query parameters; undefined and null entries are dropped. */
  params?: Record<string, string | number | boolean | undefined | null>;
  /** Internal: prevents a refresh loop. */
  _retried?: boolean;
}

/** In-flight refresh, shared by every request that hits a 401 at once. */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refreshToken = tokenStore.refresh;
  if (!refreshToken) return false;

  // Coalesce: the backend burns a refresh token on use, so concurrent refreshes
  // would revoke one another and log the user out mid-session.
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${API_V1}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) {
        tokenStore.clear();
        return false;
      }
      tokenStore.set((await response.json()) as TokenPair);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

function buildUrl(path: string, params?: RequestOptions["params"]): string {
  const url = new URL(`${API_V1}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, params, _retried, ...init } = options;

  const headers = new Headers(init.headers);
  if (body !== undefined && !(body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (auth) {
    const token = tokenStore.access;
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), {
      ...init,
      headers,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  } catch {
    throw new NetworkError();
  }

  if (response.status === 401 && auth && !_retried && tokenStore.refresh) {
    if (await refreshTokens()) {
      return request<T>(path, { ...options, _retried: true });
    }
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? safeJson(text) : null;

  if (!response.ok) {
    const error = (payload as ApiErrorBody | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? `http_${response.status}`,
      error?.message ?? response.statusText ?? "The request failed.",
      error?.details ?? {},
    );
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  auth: {
    methods: () => request<AuthMethods>("/auth/methods", { auth: false }),

    register: (input: {
      email: string;
      password: string;
      full_name: string;
      home_city?: string;
    }) =>
      request<AuthResponse>("/auth/register", {
        method: "POST",
        body: input,
        auth: false,
      }),

    login: (input: { email: string; password: string }) =>
      request<AuthResponse>("/auth/login", {
        method: "POST",
        body: input,
        auth: false,
      }),

    /** Complete a Google or Apple sign-in with the provider's ID token. */
    oauth: (
      provider: "google" | "apple",
      input: { id_token: string; nonce?: string; full_name?: string },
    ) =>
      request<AuthResponse>(`/auth/oauth/${provider}`, {
        method: "POST",
        body: input,
        auth: false,
      }),

    logout: (refreshToken: string) =>
      request<ApiMessage>("/auth/logout", {
        method: "POST",
        body: { refresh_token: refreshToken },
        auth: false,
      }),

    logoutAll: () => request<ApiMessage>("/auth/logout-all", { method: "POST" }),

    changePassword: (input: {
      current_password: string;
      new_password: string;
    }) =>
      request<ApiMessage>("/auth/change-password", { method: "POST", body: input }),

    setPassword: (newPassword: string) =>
      request<ApiMessage>("/auth/set-password", {
        method: "POST",
        body: { new_password: newPassword },
      }),

    linked: () => request<LinkedProviders>("/auth/linked"),

    unlink: (provider: "google" | "apple") =>
      request<ApiMessage>(`/auth/linked/${provider}`, { method: "DELETE" }),
  },

  users: {
    me: () => request<User>("/users/me"),

    update: (input: { full_name?: string; home_city?: string }) =>
      request<User>("/users/me", { method: "PATCH", body: input }),

    updatePreferences: (preferences: Partial<User["preferences"]>) =>
      request<User>("/users/me/preferences", { method: "PUT", body: preferences }),

    stats: () => request<UserStats>("/users/me/stats"),

    deactivate: () => request<ApiMessage>("/users/me", { method: "DELETE" }),
  },

  places: {
    list: (params: {
      city?: string;
      category?: PlaceCategory;
      tags?: string[];
      min_rating?: number;
      skip?: number;
      limit?: number;
    }) => {
      const { tags, ...rest } = params;
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(rest)) {
        if (value !== undefined && value !== null && value !== "") {
          query.set(key, String(value));
        }
      }
      // Repeated keys, which is how FastAPI reads a list query parameter.
      for (const tag of tags ?? []) query.append("tags", tag);
      return request<Page<Place>>(`/places?${query.toString()}`, { auth: false });
    },

    get: (id: string) => request<Place>(`/places/${id}`, { auth: false }),

    nearby: (params: {
      latitude: number;
      longitude: number;
      radius_km?: number;
      category?: PlaceCategory;
      min_rating?: number;
      limit?: number;
    }) => request<Place[]>("/places/nearby", { params, auth: false }),

    search: (params: { q: string; city?: string; limit?: number }) =>
      request<Place[]>("/places/search", { params, auth: false }),

    /** Search by meaning rather than keyword. */
    semanticSearch: (input: { query: string; city?: string; limit?: number }) =>
      request<Place[]>("/places/semantic-search", {
        method: "POST",
        body: input,
        auth: false,
      }),

    cities: () => request<CitySummary[]>("/places/cities", { auth: false }),
  },

  trips: {
    list: (params: { status?: string; skip?: number; limit?: number } = {}) =>
      request<Page<Trip>>("/trips", { params }),

    get: (id: string) => request<Trip>(`/trips/${id}`),

    create: (input: TripCreateInput) =>
      request<Trip>("/trips", { method: "POST", body: input }),

    update: (id: string, input: Partial<TripCreateInput>) =>
      request<Trip>(`/trips/${id}`, { method: "PATCH", body: input }),

    remove: (id: string) => request<ApiMessage>(`/trips/${id}`, { method: "DELETE" }),

    /** Run the planning pipeline. Slow by design — it solves a routing problem. */
    generate: (id: string, input: { brief?: string; regenerate?: boolean } = {}) =>
      request<Itinerary>(`/trips/${id}/generate`, { method: "POST", body: input }),

    itinerary: (id: string) => request<Itinerary>(`/trips/${id}/itinerary`),

    itineraries: (id: string) =>
      request<ItinerarySummary[]>(`/trips/${id}/itineraries`),

    share: (id: string) => request<Trip>(`/trips/${id}/share`, { method: "POST" }),

    unshare: (id: string) =>
      request<Trip>(`/trips/${id}/share`, { method: "DELETE" }),

    shared: (token: string) =>
      request<Itinerary>(`/trips/shared/${token}`, { auth: false }),
  },

  itineraries: {
    list: (params: { skip?: number; limit?: number } = {}) =>
      request<Page<ItinerarySummary>>("/itineraries", { params }),

    get: (id: string) => request<Itinerary>(`/itineraries/${id}`),

    stats: (id: string) => request<ItineraryStats>(`/itineraries/${id}/stats`),

    reorderDay: (id: string, input: { day_number: number; activity_order: number[] }) =>
      request<Itinerary>(`/itineraries/${id}/reorder`, { method: "POST", body: input }),

    /** Re-plan a set of places with several algorithms and compare the results. */
    compareAlgorithms: (input: CompareRouteInput) =>
      request<CompareRouteResponse>("/itineraries/optimize-route/compare", {
        method: "POST",
        body: input,
      }),

    algorithms: () =>
      request<AlgorithmInfo[]>("/itineraries/optimizer/algorithms"),

    removeActivity: (id: string, dayNumber: number, activityIndex: number) =>
      request<Itinerary>(
        `/itineraries/${id}/days/${dayNumber}/activities/${activityIndex}`,
        { method: "DELETE" },
      ),

    activate: (id: string) =>
      request<ApiMessage>(`/itineraries/${id}/activate`, { method: "POST" }),

    remove: (id: string) =>
      request<ApiMessage>(`/itineraries/${id}`, { method: "DELETE" }),
  },

  ai: {
    /** Show the traveller how their words were read, before planning on them. */
    parseIntent: (input: { trip_id: string; brief: string }) =>
      request<TripIntent>("/ai/parse-intent", { method: "POST", body: input }),

    recommend: (input: { query: string; city?: string; limit?: number }) =>
      request<Place[]>("/ai/recommend", { method: "POST", body: input }),

    runs: (limit = 20) => request<AIRun[]>("/ai/runs", { params: { limit } }),
  },

  health: () =>
    request<{ status: string; version: string; mongodb: string }>("/health/ready", {
      auth: false,
    }),
};

export { request as rawRequest };
