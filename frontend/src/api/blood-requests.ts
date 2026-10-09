/**
 * Blood requests API functions.
 */

import api from './client';

/**
 * How many units one request may ask for. Mirrors the backend rule in
 * `app/schemas/blood_request.py` — keep the two in step, because a mismatch
 * shows up as an unexplained 422 on submit.
 */
export const MIN_REQUEST_UNITS = 1;
export const MAX_REQUEST_UNITS = 10;

export interface BloodRequest {
  id: number;
  recipient_id: number;
  patient_name: string;
  blood_group: string;
  units: number;
  hospital_name: string;
  hospital_address?: string;
  latitude?: number;
  longitude?: number;
  needed_date: string;
  contact_number: string;
  notes?: string;
  status: string;
  accepted_by?: number;
  created_at: string;
  updated_at: string;
  recipient_name?: string;
  donor_name?: string;
  donor_phone?: string;
  distance_km?: number;
}

export interface CreateBloodRequestData {
  patient_name: string;
  blood_group: string;
  units: number;
  hospital_name: string;
  hospital_address?: string;
  latitude?: number;
  longitude?: number;
  needed_date: string;
  contact_number: string;
  notes?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export async function createBloodRequest(data: CreateBloodRequestData) {
  const { data: result } = await api.post<BloodRequest>('/blood-requests', data);
  return result;
}

/**
 * Fetch a single request.
 *
 * Visible to the recipient and the accepting donor at any status, and to any
 * other authenticated user while the request is Pending or Accepted — which is
 * what lets a donor open a request from the nearby feed or a push notification.
 * Returns 404 both when the request doesn't exist and when the caller isn't
 * allowed to see it, so request IDs can't be enumerated.
 */
export async function getBloodRequest(requestId: number) {
  const { data } = await api.get<BloodRequest>(`/blood-requests/${requestId}`);
  return data;
}

export async function acceptRequest(requestId: number) {
  const { data } = await api.post<BloodRequest>(`/blood-requests/${requestId}/accept`);
  return data;
}

export async function completeRequest(requestId: number) {
  const { data } = await api.post<BloodRequest>(`/blood-requests/${requestId}/complete`);
  return data;
}

export async function cancelRequest(requestId: number) {
  const { data } = await api.post<BloodRequest>(`/blood-requests/${requestId}/cancel`);
  return data;
}

export async function getNearbyRequests(params: {
  latitude: number;
  longitude: number;
  radius_km?: number;
  limit?: number;
  offset?: number;
}) {
  const { data } = await api.get<PaginatedResponse<BloodRequest>>('/blood-requests/nearby', { params });
  return data;
}

export async function getMyRequests(params?: { limit?: number; offset?: number }) {
  const { data } = await api.get<PaginatedResponse<BloodRequest>>('/blood-requests/mine', { params });
  return data;
}
