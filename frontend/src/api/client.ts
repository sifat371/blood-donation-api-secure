/**
 * Axios HTTP client configured for the Blood Donation API.
 *
 * Features:
 * - Auto-attaches Bearer token from auth store
 * - Auto-refreshes on 401 and retries the original request (once)
 * - Base URL discovered per-network at request time, never hardcoded
 */

import { useAuthStore } from "@/store/auth-store";
import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";
import { router } from "expo-router";
import { getResolution, withApiPath } from "./backend-url";
import { BackendUnreachableError } from "./errors";

const api = axios.create({
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

// ── Request interceptor — resolve base URL, attach access token ──────────

api.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // Resolved here rather than at module load so the app follows the backend
    // across router/DHCP changes without being rebuilt. Cached after the first
    // call, so this is a no-op on every subsequent request.
    const resolution = await getResolution();

    // Fail here instead of firing the request at an address nothing is listening
    // on. Both produce an error either way, but only this one can say *why*.
    if (!resolution.found) {
      throw new BackendUnreachableError(
        resolution.deviceIp,
        resolution.via === "offline",
      );
    }

    config.baseURL = withApiPath(resolution.origin);

    const token = useAuthStore.getState().accessToken;
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
);

// ── Response interceptor — auto-refresh on 401 ─────────

let isRefreshing = false;
let failedQueue: Array<{
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
  config: InternalAxiosRequestConfig;
}> = [];

function processQueue(error: unknown) {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(api(prom.config));
    }
  });
  failedQueue = [];
}

/**
 * Endpoints where a 401/403 is the answer, not an expired session. Refreshing
 * after a rejected login would clear state and bounce the user off the login
 * screen, hiding the actual error.
 */
function isAuthEndpoint(url?: string): boolean {
  if (!url) return false;
  return (
    url.includes("/auth/google") ||
    url.includes("/auth/dev-login") ||
    url.includes("/auth/login") ||
    url.includes("/auth/signup") ||
    url.includes("/auth/verify-email") ||
    url.includes("/auth/resend-verification") ||
    url.includes("/auth/refresh")
  );
}

async function signOutAndRedirect() {
  await useAuthStore.getState().logout();
  router.replace("/");
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config;
    if (!originalRequest || error.response?.status !== 401) {
      return Promise.reject(error);
    }

    if (isAuthEndpoint(originalRequest.url)) {
      return Promise.reject(error);
    }

    // Avoid infinite loops
    if ((originalRequest as any)._retry) {
      await signOutAndRedirect();
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject, config: originalRequest });
      });
    }

    (originalRequest as any)._retry = true;
    isRefreshing = true;

    try {
      const refreshToken = useAuthStore.getState().refreshToken;
      if (!refreshToken) {
        await signOutAndRedirect();
        return Promise.reject(error);
      }

      // Deliberately a bare axios call: going through `api` would re-enter this
      // same interceptor on a 401 and recurse. It still resolves the origin
      // through `getResolution`, so it can never fall back to a stale
      // build-time URL — the one bypass that would break refresh on a phone
      // whose backend address has changed.
      const resolution = await getResolution();
      if (!resolution.found) {
        // Keep the session. The tokens are probably fine; the network is not.
        throw new BackendUnreachableError(
          resolution.deviceIp,
          resolution.via === "offline",
        );
      }

      const { data } = await axios.post(
        `${withApiPath(resolution.origin)}/auth/refresh`,
        { refresh_token: refreshToken },
      );

      // Awaited on purpose. The backend rotates refresh tokens single-use and
      // treats a replay as theft, revoking every token for the account. If the
      // app were killed between here and the SecureStore write, the next launch
      // would present the consumed token and lock the user out for good.
      await useAuthStore
        .getState()
        .setTokens(data.access_token, data.refresh_token);

      if (originalRequest.headers) {
        originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
      }

      processQueue(null);
      return api(originalRequest);
    } catch (refreshError) {
      processQueue(refreshError);
      // Only a rejected refresh means the session is really gone. A network
      // failure says nothing about the tokens, and signing out over a dropped
      // Wi-Fi connection would make the app lose sessions for no reason.
      const rejected =
        axios.isAxiosError(refreshError) && refreshError.response !== undefined;
      if (rejected) {
        await signOutAndRedirect();
      }
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  },
);

export default api;
