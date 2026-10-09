/**
 * Donors API functions.
 */

import api from './client';
import { PaginatedResponse } from './blood-requests';

export interface Donor {
  id: number;
  name: string;
  distance_km: number;
  phone?: string;
  blood_group: string;
  last_donation_date?: string;
  is_available: boolean;
  division?: string;
  district?: string;
  upazila?: string;
}

export async function searchDonors(params: {
  blood_group: string;
  latitude: number;
  longitude: number;
  radius_km?: number;
  limit?: number;
  offset?: number;
}) {
  const { data } = await api.get<PaginatedResponse<Donor>>('/donors/search', { params });
  return data;
}
