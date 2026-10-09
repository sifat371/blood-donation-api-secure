/**
 * Resolves the backend origin for whatever LAN the phone is currently on.
 *
 * Why this exists rather than a stable hostname: Android's system resolver does
 * not implement mDNS, so a "<name>.local" address cannot be resolved from a
 * release APK (verified on-device: `ping name.local` -> "unknown host"), and
 * consumer routers do not reliably register DHCP client hostnames in their own
 * DNS. So no fixed name survives an arbitrary router change. What does survive
 * is looking for the backend on the subnet the phone is already attached to.
 *
 * Resolution order — each candidate is confirmed with GET /health before it is
 * trusted, so a stale address never leaves every request hanging:
 *
 *   1. EXPO_PUBLIC_API_URL — explicit override, and the production fast path.
 *   2. Metro's hostUri — development only; absent from a release APK.
 *   3. The origin that worked last time, remembered across launches.
 *   4. A probe sweep of the phone's own /24 looking for /health.
 *
 * If none of those answer, resolution reports `found: false` and the API client
 * fails the request immediately with a specific "no backend on this network"
 * error. It deliberately does *not* fall back to localhost on a device: nothing
 * listens there, so the attempt would only turn a diagnosable condition into a
 * generic connection error.
 *
 * Probe budgets are deliberately generous on first contact. A phone that has not
 * talked to the PC yet has to resolve its MAC by broadcast ARP first, and on a
 * power-saving Wi-Fi link that alone can take more than a second — which is the
 * measured reason a release APK could fail to find a backend the same PC could
 * reach instantly.
 *
 * `process.env.EXPO_PUBLIC_API_URL` must be written exactly like this: Expo
 * inlines it at bundle time by static text substitution, so destructuring or
 * bracket access would silently produce undefined.
 */

import Constants from "expo-constants";
import * as Network from "expo-network";
import * as SecureStore from "expo-secure-store";
import { NativeModules, Platform } from "react-native";

const API_PATH = "/api/v1";
const DEFAULT_PORT = 8000;

/** Where the last known-good origin is remembered between launches. */
const CACHE_KEY = "lan_backend_origin";

/**
 * Probe budgets.
 *
 * FIRST_CONTACT covers the preferred hosts, where the cost of waiting is one
 * timeout and the cost of giving up early is failing to find the backend at all.
 * SWEEP covers the remaining 241 addresses, where 241 x FIRST_CONTACT would be
 * far too slow and almost every probe is genuinely answering nothing.
 */
const FIRST_CONTACT_TIMEOUT_MS = 1_800;
const SWEEP_TIMEOUT_MS = 700;
const KNOWN_HOST_TIMEOUT_MS = 2_500;
const RETRY_TIMEOUT_MS = 3_000;
const SCAN_CONCURRENCY = 24;

/**
 * How long a failed lookup is remembered. Long enough that a burst of requests
 * does not each trigger a sweep, short enough that starting the backend after
 * the app is already open recovers on the next pull-to-refresh.
 */
const FAILURE_COOLDOWN_MS = 20_000;

/** Where a resolved origin came from. Reported for diagnostics only. */
export type ResolutionSource =
  | "env"
  | "metro"
  | "cache"
  | "scan"
  | "scan-retry"
  | "offline"
  | "none";

/** A resolution result, plus whether a backend actually answered. */
export interface Resolution {
  origin: string;
  found: boolean;
  via: ResolutionSource;
  /** The phone's own IPv4 address, when the platform would report one. */
  deviceIp: string | null;
  /** Wall-clock cost of the resolution, for spotting slow sweeps in logcat. */
  elapsedMs: number;
}

/** Strip any trailing slash and the API path, leaving a bare origin. */
function toOrigin(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "");
  return trimmed.endsWith(API_PATH)
    ? trimmed.slice(0, -API_PATH.length)
    : trimmed;
}

/** Turn an origin into the full API base the axios client should use. */
export function withApiPath(origin: string): string {
  return `${toOrigin(origin)}${API_PATH}`;
}

/**
 * Extract the Metro bundler host from React Native's native script URL.
 *
 * In a bare `npx expo run:android` build (without expo-dev-client),
 * `Constants.expoConfig?.hostUri` is undefined because the dev-client manifest
 * server is absent.  The JS bundle URL, however, is always available through
 * `NativeSourceCode.getConstants().scriptURL` and contains the actual LAN IP of
 * the development laptop — the same machine that runs the API backend.
 *
 * Returns just the hostname (e.g. "192.168.0.107"), or null when the bundle was
 * not loaded from a network server (release builds, pre-bundled assets).
 */
function getMetroHost(): string | null {
  if (!__DEV__) return null;
  try {
    const sourceCode =
      NativeModules.SourceCode ??
      NativeModules.RNCSourceCode ??
      null;
    const scriptURL: string | undefined =
      sourceCode?.getConstants?.()?.scriptURL ??
      sourceCode?.scriptURL;
    if (!scriptURL) return null;
    const match = scriptURL.match(/^https?:\/\/([^:/]+)/);
    return match?.[1] ?? null;
  } catch {
    return null;
  }
}

/**
 * Ask a candidate whether it is our backend.
 *
 * `/health` is unauthenticated and returns `{ status: "healthy", ... }`, so it
 * both proves reachability and distinguishes our server from some unrelated
 * service that happens to be listening on port 8000.
 */
async function isBackend(origin: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${toOrigin(origin)}/health`, {
      method: "GET",
      signal: controller.signal,
    });
    if (!response.ok) return false;
    const body = (await response.json()) as { status?: string };
    return body?.status === "healthy";
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Hosts worth trying before the rest of the subnet: `.1` is the gateway and is
 * also the PC when the PC itself is the access point, and desktops tend to land
 * low in the DHCP pool or just above `.100`.
 */
const PREFERRED_HOSTS = [1, 2, 3, 4, 5, 100, 101, 102, 103, 104, 105, 106, 107];

function remainingHosts(): number[] {
  const rest: number[] = [];
  for (let host = 1; host <= 254; host += 1) {
    if (!PREFERRED_HOSTS.includes(host)) rest.push(host);
  }
  return rest;
}

/**
 * Run `task` over `items` with a bounded number in flight and return the first
 * hit, without waiting for the probes that are still timing out.
 */
function findFirst<T>(
  items: T[],
  task: (item: T) => Promise<string | null>,
): Promise<string | null> {
  return new Promise((resolve) => {
    const workerCount = Math.min(SCAN_CONCURRENCY, items.length);
    if (workerCount === 0) {
      resolve(null);
      return;
    }

    let index = 0;
    let finished = 0;
    let settled = false;

    function settle(value: string | null): void {
      if (settled) return;
      settled = true;
      resolve(value);
    }

    async function worker(): Promise<void> {
      while (!settled) {
        const current = index;
        index += 1;
        if (current >= items.length) break;
        const result = await task(items[current]);
        if (result !== null) {
          settle(result);
          return;
        }
      }
      finished += 1;
      if (finished === workerCount) settle(null);
    }

    for (let i = 0; i < workerCount; i += 1) void worker();
  });
}

/** The phone's own /24 prefix and host number, or null if it has no LAN address. */
async function localSubnet(): Promise<{ prefix: string; ownHost: number } | null> {
  let ownIp: string;
  try {
    // Documented to resolve to "0.0.0.0" rather than throw when the platform
    // cannot read the interface address.
    ownIp = await Network.getIpAddressAsync();
  } catch {
    return null;
  }

  const parts = ownIp?.split(".");
  if (!parts || parts.length !== 4) return null;
  if (parts[0] === "0" || parts[0] === "127") return null;
  if (parts.some((part) => !/^\d{1,3}$/.test(part))) return null;

  return { prefix: parts.slice(0, 3).join("."), ownHost: Number(parts[3]) };
}

function probeHosts(
  prefix: string,
  hosts: number[],
  timeoutMs: number,
): Promise<string | null> {
  return findFirst(hosts, async (host) => {
    const origin = `http://${prefix}.${host}:${DEFAULT_PORT}`;
    return (await isBackend(origin, timeoutMs)) ? origin : null;
  });
}

/**
 * Sweep the phone's own /24 for the backend, preferred addresses first and with
 * a budget that survives first-contact ARP resolution.
 *
 * expo-network exposes no subnet mask in SDK 57, so /24 is assumed. That covers
 * every default home router and the PC-hotspot case (10.42.0.0/24); a site using
 * a wider prefix needs EXPO_PUBLIC_API_URL instead.
 */
async function scanLocalSubnet(
  deep: boolean,
): Promise<{ origin: string; prefix: string } | null> {
  const subnet = await localSubnet();
  if (!subnet) return null;

  const { prefix, ownHost } = subnet;
  const preferred = PREFERRED_HOSTS.filter((host) => host !== ownHost);

  const quick = await probeHosts(
    prefix,
    preferred,
    deep ? RETRY_TIMEOUT_MS : FIRST_CONTACT_TIMEOUT_MS,
  );
  if (quick) return { origin: quick, prefix };

  const rest = remainingHosts().filter((host) => host !== ownHost);
  const swept = await probeHosts(
    prefix,
    rest,
    deep ? FIRST_CONTACT_TIMEOUT_MS : SWEEP_TIMEOUT_MS,
  );
  return swept ? { origin: swept, prefix } : null;
}

async function readCache(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(CACHE_KEY);
  } catch {
    return null;
  }
}

async function writeCache(origin: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(CACHE_KEY, origin);
  } catch {
    // A missing cache only costs one extra sweep next launch.
  }
}

/** True when the device reports no usable network at all. */
async function isOffline(): Promise<boolean> {
  try {
    const state = await Network.getNetworkStateAsync();
    return state.isConnected === false;
  } catch {
    return false;
  }
}

/** Work through the resolution order and return the first origin that answers. */
async function resolveOrigin(): Promise<Resolution> {
  const startedAt = Date.now();
  const finish = (
    origin: string,
    found: boolean,
    via: ResolutionSource,
    deviceIp: string | null,
  ): Resolution => ({
    origin,
    found,
    via,
    deviceIp,
    elapsedMs: Date.now() - startedAt,
  });

  const configured = process.env.EXPO_PUBLIC_API_URL;
  if (configured && configured.trim() && configured.trim() !== "auto") {
    const origin = toOrigin(configured);
    if (await isBackend(origin, KNOWN_HOST_TIMEOUT_MS)) {
      return finish(origin, true, "env", null);
    }
  }

  // Development only: the Metro bundler host is the same machine running the
  // backend, just on a different port.  Two sources are tried:
  //
  //   1. Constants.expoConfig.hostUri — injected by expo-dev-client.
  //   2. NativeSourceCode.scriptURL — always present when the JS bundle is
  //      loaded from a network server (bare `npx expo run:android`).
  //
  // A release APK has neither, so both branches are dead there.
  const hostUri = Constants.expoConfig?.hostUri;
  const metroHost =
    hostUri?.split("/")[0].split(":")[0] || getMetroHost();

  if (metroHost) {
    const origin = `http://${metroHost}:${DEFAULT_PORT}`;
    if (await isBackend(origin, KNOWN_HOST_TIMEOUT_MS)) {
      return finish(origin, true, "metro", null);
    }
  }

  const cached = await readCache();
  if (cached && (await isBackend(cached, KNOWN_HOST_TIMEOUT_MS))) {
    return finish(cached, true, "cache", null);
  }

  const subnet = await localSubnet();
  const deviceIp = subnet ? `${subnet.prefix}.${subnet.ownHost}` : null;

  const discovered = await scanLocalSubnet(false);
  if (discovered) {
    await writeCache(discovered.origin);
    return finish(discovered.origin, true, "scan", deviceIp);
  }

  // One retry with a longer budget before declaring the backend absent. On a
  // cold Wi-Fi link the first sweep can spend its whole budget waiting for ARP
  // to resolve; by now the neighbour table is warm, so a second pass is much
  // more likely to land. This is the single change that makes discovery survive
  // a phone that has just joined the network.
  const retried = await scanLocalSubnet(true);
  if (retried) {
    await writeCache(retried.origin);
    return finish(retried.origin, true, "scan-retry", deviceIp);
  }

  if (await isOffline()) {
    return finish(`http://localhost:${DEFAULT_PORT}`, false, "offline", deviceIp);
  }

  return finish(`http://localhost:${DEFAULT_PORT}`, false, "none", deviceIp);
}

let pending: Promise<Resolution> | null = null;
let last: Resolution | null = null;

/**
 * A one-line, non-secret description of the last resolution.
 *
 * Contains only the origin, how it was found, the device's own LAN address and
 * the elapsed time — no tokens, credentials or user data. This is what makes an
 * on-device failure diagnosable from `adb logcat` without a debug build.
 */
export function describeResolution(resolution: Resolution): string {
  const parts = [
    `origin=${resolution.origin}`,
    `found=${resolution.found}`,
    `via=${resolution.via}`,
    `deviceIp=${resolution.deviceIp ?? "unknown"}`,
    `ms=${resolution.elapsedMs}`,
  ];
  return `[api] backend resolution ${parts.join(" ")}`;
}

/** The last resolution, for diagnostics screens. Null before the first request. */
export function getLastResolution(): Resolution | null {
  return last;
}

/**
 * Resolve the backend for the current network, once, and reuse the result.
 *
 * Concurrent callers share a single resolution, so a cold start does not fan out
 * one subnet sweep per in-flight request. A successful result is kept for the
 * life of the process; a failure is kept only briefly, so that starting the
 * backend after the app is already open recovers on its own.
 */
export function getResolution(): Promise<Resolution> {
  if (!pending) {
    const attempt = resolveOrigin().catch(
      (): Resolution => ({
        origin: `http://localhost:${DEFAULT_PORT}`,
        found: false,
        via: "none",
        deviceIp: null,
        elapsedMs: 0,
      }),
    );

    pending = attempt.then((resolution) => {
      last = resolution;
      // Logged in release builds too: without it, "the app cannot reach the
      // server" is unfalsifiable on a device that has no debugger attached.
      // Safe to keep — see describeResolution for exactly what it prints.
      console.log(describeResolution(resolution));

      if (!resolution.found) {
        const stale = pending;
        setTimeout(() => {
          if (pending === stale) pending = null;
        }, FAILURE_COOLDOWN_MS);
      }
      return resolution;
    });
  }
  return pending;
}

/**
 * The API base URL for the current network.
 *
 * Kept for callers that only need a URL. Anything that must distinguish "no
 * backend found" from "the backend said no" should use `getResolution()`.
 */
export async function getBaseUrl(): Promise<string> {
  const { origin } = await getResolution();
  return withApiPath(origin);
}

/**
 * Forget the resolved address so the next request resolves again.
 *
 * Called when the device changes network, which is exactly when the backend's
 * address is likely to have changed too.
 */
export function invalidateBaseUrl(): void {
  pending = null;
}

/** Drop the remembered origin as well. For a "re-detect server" action. */
export async function forgetCachedOrigin(): Promise<void> {
  invalidateBaseUrl();
  try {
    await SecureStore.deleteItemAsync(CACHE_KEY);
  } catch {
    // Nothing cached, nothing to do.
  }
}

/** Native devices have no localhost backend; web and simulators do. */
export const localhostIsPlausible = Platform.OS === "web";

// Joining a different Wi-Fi almost always means a different subnet, so drop the
// resolved address and let the next request find the backend again.
Network.addNetworkStateListener(() => {
  invalidateBaseUrl();
});
