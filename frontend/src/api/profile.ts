/**
 * Profile API functions.
 */

import { PaginatedResponse } from "./blood-requests";
import api from "./client";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

export interface UserProfile {
  id: number;
  name: string;
  email: string;
  profile_photo?: string;
  phone?: string;
  blood_group?: string;
  division?: string;
  district?: string;
  upazila?: string;
  latitude?: number;
  longitude?: number;
  last_donation_date?: string;
  is_available: boolean;
  gender?: string;
  date_of_birth?: string;
  created_at: string;
  updated_at: string;
}

export interface ProfileUpdateData {
  name?: string;
  phone?: string;
  blood_group?: string;
  division?: string;
  district?: string;
  upazila?: string;
  latitude?: number;
  longitude?: number;
  is_available?: boolean;
  gender?: string;
  date_of_birth?: string;
  profile_photo?: string;
}

export interface DonationHistory {
  id: number;
  donor_id: number;
  request_id?: number;
  date: string;
  recipient?: string;
  hospital?: string;
  blood_group: string;
  status: string;
  created_at: string;
}

export async function getMyProfile() {
  const { data } = await api.get<UserProfile>("/profile/me");
  return data;
}

export async function updateProfile(updates: ProfileUpdateData) {
  const { data } = await api.patch<UserProfile>("/profile/me", updates);
  return data;
}

/**
 * Complete the profile after first sign-in.
 *
 * `google_id` is deliberately not part of this payload: the server sets it from
 * the verified Google ID token at login, and accepting it here would let a
 * client repoint its own account. FCM tokens go through registerFcmToken, which
 * hits the endpoint that actually stores them (one row per device).
 *
 * Sending `null` for latitude/longitude leaves any previously saved coordinates
 * untouched rather than clearing them.
 */
export async function completeProfile(profileData: {
  phone: string;
  blood_group: string;
  division: string;
  district: string;
  upazila: string;
  gender: string;
  date_of_birth: string;
  latitude: number | null;
  longitude: number | null;
}) {
  const { data } = await api.post<UserProfile>(
    "/profile/complete",
    profileData,
  );
  return data;
}

const DEVICE_ID_KEY = "blood_connect_installation_id";

/** Stable per-installation identity (not a credential). */
export async function getInstallationId(): Promise<string> {
  let id = await SecureStore.getItemAsync(DEVICE_ID_KEY);
  if (!id) {
    id = `installation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    await SecureStore.setItemAsync(DEVICE_ID_KEY, id);
  }
  return id;
}

export async function registerFcmToken(fcmToken: string) {
  const deviceId = await getInstallationId();
  const { data } = await api.post<{ message: string }>("/profile/fcm-token", {
    device_id: deviceId,
    fcm_token: fcmToken,
    device_info: Platform.OS,
  });
  return data;
}
export async function unregisterFcmDevice() {
  const id = await SecureStore.getItemAsync(DEVICE_ID_KEY);
  if (id) await api.delete(`/profile/fcm-token/${encodeURIComponent(id)}`);
}

export async function getDonationHistory(params?: {
  limit?: number;
  offset?: number;
}) {
  const { data } = await api.get<PaginatedResponse<DonationHistory>>(
    "/profile/donation-history",
    { params },
  );
  return data;
}
