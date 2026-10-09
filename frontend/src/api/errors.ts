/**
 * One place that decides what went wrong, so every screen can say something
 * true instead of guessing.
 *
 * Before this existed, thirteen screens printed the same sentence — "Can't reach
 * the server. Check that the backend is running and EXPO_PUBLIC_API_URL points
 * at it" — for every possible failure, including ones where the server had
 * answered perfectly clearly. A wrong password, an unverified email and a
 * genuinely unreachable backend are three different problems with three
 * different fixes, and telling the user to check an environment variable they
 * have never heard of is wrong in all but one of them.
 *
 * Messages here are safe to show: no internal addresses, stack traces, tokens,
 * or field-level backend detail.
 */

import axios from "axios";

import { getLastResolution } from "./backend-url";

export type ApiErrorKind =
  | "NETWORK_UNREACHABLE"
  | "OFFLINE"
  | "TIMEOUT"
  | "AUTHENTICATION_FAILED"
  | "GOOGLE_AUTH_FAILED"
  | "EMAIL_NOT_VERIFIED"
  | "ACCOUNT_EXISTS"
  | "NOT_FOUND"
  | "VALIDATION_ERROR"
  | "RATE_LIMITED"
  | "SERVER_ERROR"
  | "SESSION_EXPIRED"
  | "CANCELLED"
  | "UNKNOWN_ERROR";

export interface ClassifiedError {
  kind: ApiErrorKind;
  /** Shown to the user. Complete sentence, no jargon. */
  message: string;
  /** The backend's own code when it sent one, for callers that must branch. */
  code?: string;
  status?: number;
}

/**
 * Thrown by the API client when no backend could be found on the current
 * network, instead of attempting a request against an address nothing is
 * listening on. Carries the diagnostic detail that makes the failure actionable.
 */
export class BackendUnreachableError extends Error {
  readonly deviceIp: string | null;
  readonly offline: boolean;

  constructor(deviceIp: string | null, offline: boolean) {
    super(
      offline
        ? "This device is not connected to a network."
        : "No Blood Donation server was found on this Wi-Fi network.",
    );
    this.name = "BackendUnreachableError";
    this.deviceIp = deviceIp;
    this.offline = offline;
  }
}

/** Google Sign-In failed before the backend was ever contacted. */
export class GoogleSignInError extends Error {
  readonly reason: "no-id-token" | "cancelled" | "play-services" | "failed";

  constructor(reason: GoogleSignInError["reason"], message: string) {
    super(message);
    this.name = "GoogleSignInError";
    this.reason = reason;
  }
}

const NETWORK_HELP =
  "No Blood Donation server was found on this Wi-Fi network. Make sure the " +
  "phone and the server are on the same Wi-Fi, then try again.";

/** Pull `{ detail: { code, message } }` out of a response body, if present. */
function readDetail(data: unknown): { code?: string; message?: string } {
  if (!data || typeof data !== "object") return {};
  const detail = (data as { detail?: unknown }).detail;

  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    return {
      code: typeof code === "string" ? code : undefined,
      message: typeof message === "string" ? message : undefined,
    };
  }

  // FastAPI's own validation errors arrive as detail: [{ loc, msg, ... }]. Their
  // wording names internal field paths, so only the first message is surfaced
  // and only when it is the one thing the user can act on.
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: unknown; loc?: unknown } | undefined;
    const field = Array.isArray(first?.loc)
      ? String(first?.loc[first!.loc.length - 1])
      : undefined;
    if (field === "email") {
      return { message: "That email address is not valid." };
    }
    return { message: "Please check the details you entered." };
  }

  if (typeof detail === "string") return { message: detail };
  return {};
}

function classifyStatus(status: number, code?: string): ApiErrorKind {
  if (code === "EMAIL_NOT_VERIFIED") return "EMAIL_NOT_VERIFIED";
  if (code === "GOOGLE_AUTH_FAILED") return "GOOGLE_AUTH_FAILED";
  if (code === "SESSION_EXPIRED") return "SESSION_EXPIRED";
  if (code === "EMAIL_ALREADY_REGISTERED" || code === "ACCOUNT_EXISTS_GOOGLE") {
    return "ACCOUNT_EXISTS";
  }

  if (status === 400 || status === 422) return "VALIDATION_ERROR";
  if (status === 401) return "AUTHENTICATION_FAILED";
  if (status === 403) return "AUTHENTICATION_FAILED";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "ACCOUNT_EXISTS";
  if (status === 429) return "RATE_LIMITED";
  if (status >= 500) return "SERVER_ERROR";
  return "UNKNOWN_ERROR";
}

function fallbackMessage(kind: ApiErrorKind): string {
  switch (kind) {
    case "NETWORK_UNREACHABLE":
      return NETWORK_HELP;
    case "OFFLINE":
      return "This device is not connected to a network. Turn on Wi-Fi and try again.";
    case "TIMEOUT":
      return "The server took too long to respond. Please try again.";
    case "AUTHENTICATION_FAILED":
      return "Incorrect email or password.";
    case "GOOGLE_AUTH_FAILED":
      return "Google sign-in did not complete. Please try again.";
    case "EMAIL_NOT_VERIFIED":
      return "Please verify your email before signing in.";
    case "ACCOUNT_EXISTS":
      return "An account with this email already exists.";
    case "NOT_FOUND":
      return "That item no longer exists.";
    case "VALIDATION_ERROR":
      return "Please check the details you entered.";
    case "RATE_LIMITED":
      return "Too many attempts. Please wait a minute and try again.";
    case "SERVER_ERROR":
      return "The server ran into a problem. Please try again shortly.";
    case "SESSION_EXPIRED":
      return "Your session has expired. Please sign in again.";
    case "CANCELLED":
      return "Cancelled.";
    default:
      return "Something went wrong. Please try again.";
  }
}

/** Work out what actually failed. Never throws. */
export function classifyError(error: unknown): ClassifiedError {
  if (error instanceof BackendUnreachableError) {
    return {
      kind: error.offline ? "OFFLINE" : "NETWORK_UNREACHABLE",
      message: error.offline ? fallbackMessage("OFFLINE") : NETWORK_HELP,
    };
  }

  if (error instanceof GoogleSignInError) {
    return {
      kind: error.reason === "cancelled" ? "CANCELLED" : "GOOGLE_AUTH_FAILED",
      message: error.message,
    };
  }

  if (axios.isAxiosError(error)) {
    if (error.response) {
      const { status } = error.response;
      const { code, message } = readDetail(error.response.data);
      const kind = classifyStatus(status, code);
      return { kind, code, status, message: message || fallbackMessage(kind) };
    }

    if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
      return { kind: "TIMEOUT", message: fallbackMessage("TIMEOUT") };
    }

    // No response and not a timeout: the request never reached a server. Report
    // what resolution actually concluded rather than guessing at a cause.
    const resolution = getLastResolution();
    if (resolution && !resolution.found) {
      return {
        kind: resolution.via === "offline" ? "OFFLINE" : "NETWORK_UNREACHABLE",
        message:
          resolution.via === "offline"
            ? fallbackMessage("OFFLINE")
            : NETWORK_HELP,
      };
    }
    return {
      kind: "NETWORK_UNREACHABLE",
      message:
        "The server stopped responding. Check that it is still running, then try again.",
    };
  }

  if (error instanceof Error && error.message) {
    return { kind: "UNKNOWN_ERROR", message: error.message };
  }

  return { kind: "UNKNOWN_ERROR", message: fallbackMessage("UNKNOWN_ERROR") };
}

/** The user-facing sentence for any failure. The common case. */
export function describeError(error: unknown): string {
  return classifyError(error).message;
}
