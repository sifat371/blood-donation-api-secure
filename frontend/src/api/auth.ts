/**
 * Auth API functions.
 */

import axios from "axios";
import { getResolution, withApiPath } from "./backend-url";
import api from "./client";
import { BackendUnreachableError } from "./errors";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  profile_incomplete: boolean;
}

export interface SignupPayload {
  name: string;
  email: string;
  password: string;
  phone?: string;
  blood_group?: string;
  gender?: string;
}

/** Signup deliberately returns no session — the email has to be verified first. */
export interface SignupResponse {
  email: string;
  email_verified: boolean;
  message: string;
  /** "smtp" when really mailed, "outbox" on a development server. */
  delivery: string;
}

export interface VerifyEmailResponse {
  email: string;
  email_verified: boolean;
  message: string;
}

export interface ResendVerificationResponse {
  message: string;
  delivery?: string | null;
}

export async function googleLogin(idToken: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/auth/google", {
    id_token: idToken,
  });
  return data;
}

export async function signup(payload: SignupPayload): Promise<SignupResponse> {
  const { data } = await api.post<SignupResponse>("/auth/signup", payload);
  return data;
}

export async function emailLogin(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/auth/login", {
    email,
    password,
  });
  return data;
}

export async function verifyEmail(
  email: string,
  code: string,
): Promise<VerifyEmailResponse> {
  const { data } = await api.post<VerifyEmailResponse>("/auth/verify-email", {
    email,
    code,
  });
  return data;
}

export async function resendVerification(
  email: string,
): Promise<ResendVerificationResponse> {
  const { data } = await api.post<ResendVerificationResponse>(
    "/auth/resend-verification",
    { email },
  );
  return data;
}

export async function devLogin(
  email: string,
  name?: string,
): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/auth/dev-login", {
    email,
    name: name || "Dev User",
  });
  return data;
}

export async function refreshToken(token: string): Promise<TokenResponse> {
  // Deliberately bare axios: going through `api` would re-enter the 401
  // interceptor. The base URL still has to be discovered, because session
  // restoration on launch runs before any other request — and it must come from
  // the same resolver as everything else, never a build-time constant.
  const resolution = await getResolution();
  if (!resolution.found) {
    throw new BackendUnreachableError(
      resolution.deviceIp,
      resolution.via === "offline",
    );
  }

  const { data } = await axios.post<TokenResponse>(
    `${withApiPath(resolution.origin)}/auth/refresh`,
    { refresh_token: token },
  );
  return data;
}

export async function logoutApi(token: string): Promise<void> {
  await api.post("/auth/logout", { refresh_token: token });
}
