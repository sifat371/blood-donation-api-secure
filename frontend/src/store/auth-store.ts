/**
 * Auth state management using Zustand.
 *
 * Stores access token (in memory), refresh token, user profile,
 * and auth flow state.
 */

import { UserProfile } from "@/api/profile";
import { create } from "zustand";
import { clearTokens, saveTokens } from "./auth-storage";

interface AuthState {
  // State
  accessToken: string | null;
  refreshToken: string | null;
  googleID: string | null;
  user: UserProfile | null;
  isAuthenticated: boolean;
  isProfileComplete: boolean;
  isLoading: boolean;

  // Actions
  /**
   * Store a token pair in memory *and* in SecureStore.
   *
   * Persistence lives here rather than at the call sites because the refresh
   * interceptor previously updated only the in-memory copy. SecureStore kept the
   * consumed refresh token, so the next app launch presented a token the server
   * had already rotated away — and since the backend treats a replayed refresh
   * token as theft, it revoked every token for that account. One missed write
   * turned into a permanent logout.
   */
  setTokens: (access: string, refresh: string) => Promise<void>;
  /** Populate from SecureStore at startup without writing the same values back. */
  hydrateTokens: (access: string, refresh: string) => void;
  setGoogleID: (googleID: string) => void;
  setUser: (user: UserProfile) => void;
  setProfileComplete: (complete: boolean) => void;
  setLoading: (loading: boolean) => void;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  refreshToken: null,
  googleID: null,
  user: null,
  isAuthenticated: false,
  isProfileComplete: false,
  isLoading: true,

  setTokens: async (access, refresh) => {
    set({
      accessToken: access,
      refreshToken: refresh,
      isAuthenticated: true,
    });
    try {
      await saveTokens(access, refresh);
    } catch (error) {
      // The session still works for now; it just won't survive a restart.
      console.warn("Failed to persist auth tokens to SecureStore", error);
    }
  },

  hydrateTokens: (access, refresh) =>
    set({
      accessToken: access,
      refreshToken: refresh,
      isAuthenticated: true,
    }),

  setGoogleID: (googleID) => set({ googleID }),

  setUser: (user) =>
    set({
      user,
      isProfileComplete: !!(user.blood_group && user.phone),
    }),

  setProfileComplete: (complete) => set({ isProfileComplete: complete }),

  setLoading: (loading) => set({ isLoading: loading }),

  logout: async () => {
    await clearTokens();
    set({
      accessToken: null,
      refreshToken: null,
      googleID: null,
      user: null,
      isAuthenticated: false,
      isProfileComplete: false,
    });
  },
}));
