/**
 * Profile API functions.
 */

import { PaginatedResponse } from "./blood-requests";
import api from "./client";

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

export async function registerFcmToken(fcmToken: string) {
  const { data } = await api.post<{ message: string }>("/profile/fcm-token", {
    fcm_token: fcmToken,
  });
  return data;
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
