"use client";

/**
 * Session state.
 *
 * Zustand rather than React context: the header, the plan wizard and half a
 * dozen dialogs all read the current user, and context would re-render every
 * one of them whenever any slice of the session changed.
 *
 * The tokens themselves live in `tokenStore` (localStorage), not here. This
 * store holds the *user*, which is derived from them — keeping one owner for
 * the credentials avoids the two drifting apart.
 */

import { create } from "zustand";

import { ApiError, api, tokenStore } from "@/lib/api";
import type { AuthResponse, User } from "@/types/api";

interface AuthState {
  user: User | null;
  /** True until the first session check finishes, so the UI can hold its shape. */
  isLoading: boolean;
  isAuthenticated: boolean;

  /** Restore a session from stored tokens. Safe to call repeatedly. */
  bootstrap: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<User>;
  signUp: (input: {
    email: string;
    password: string;
    full_name: string;
    home_city?: string;
  }) => Promise<User>;
  signInWithProvider: (
    provider: "google" | "apple",
    input: { id_token: string; nonce?: string; full_name?: string },
  ) => Promise<User>;
  signOut: () => Promise<void>;
  setUser: (user: User | null) => void;
  refreshUser: () => Promise<void>;
}

/** Shared by every sign-in path, so none of them can forget a step. */
function adopt(set: (partial: Partial<AuthState>) => void, response: AuthResponse) {
  tokenStore.set(response.tokens);
  set({ user: response.user, isAuthenticated: true, isLoading: false });
  return response.user;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isLoading: true,
  isAuthenticated: false,

  async bootstrap() {
    if (!tokenStore.access && !tokenStore.refresh) {
      set({ user: null, isAuthenticated: false, isLoading: false });
      return;
    }
    try {
      const user = await api.users.me();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch (error) {
      // A 401 here means the stored tokens are dead; anything else (the API
      // being down, say) should not silently wipe a valid session.
      if (error instanceof ApiError && error.isAuth) {
        tokenStore.clear();
        set({ user: null, isAuthenticated: false });
      }
      set({ isLoading: false });
    }
  },

  async signIn(email, password) {
    const response = await api.auth.login({ email, password });
    return adopt(set, response);
  },

  async signUp(input) {
    const response = await api.auth.register(input);
    return adopt(set, response);
  },

  async signInWithProvider(provider, input) {
    const response = await api.auth.oauth(provider, input);
    return adopt(set, response);
  },

  async signOut() {
    const refresh = tokenStore.refresh;
    // Clear locally first: the user asked to leave, and that should happen even
    // if the revoke call fails or the network is down.
    tokenStore.clear();
    set({ user: null, isAuthenticated: false, isLoading: false });
    if (refresh) {
      try {
        await api.auth.logout(refresh);
      } catch {
        /* The token expires on its own; nothing more to do. */
      }
    }
  },

  setUser(user) {
    set({ user, isAuthenticated: Boolean(user) });
  },

  async refreshUser() {
    if (!get().isAuthenticated) return;
    try {
      set({ user: await api.users.me() });
    } catch {
      /* Keep the cached user rather than flashing a signed-out header. */
    }
  },
}));

/** Convenience selector for components that only need the user. */
export const useUser = () => useAuthStore((state) => state.user);
export const useIsAuthenticated = () =>
  useAuthStore((state) => state.isAuthenticated);
