
/** Typed REST contract matching the FastAPI P2/F0 multi-donor implementation. */
import api from './client';

export const MIN_REQUEST_UNITS = 1;
export const MAX_REQUEST_UNITS = 10;

export type RequestStatus =
  | 'Pending' | 'Partially Committed' | 'Fully Committed'
  | 'Completed' | 'Cancelled' | 'Expired';

export type CommitmentStatus = 'Committed' | 'Completed' | 'Withdrawn' | 'Cancelled';

export interface BloodRequest {
  id: number;
  recipient_id: number | null;
  patient_name: string | null;
  blood_group: string;
  units: number;
  units_required: number;
  units_committed: number;
  units_completed: number;
  remaining_units: number;
  hospital_name: string;
  hospital_address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  needed_date: string;
  contact_number: string | null;
  notes?: string | null;
  status: RequestStatus;
  my_commitment_status?: CommitmentStatus | null;
  accepted_by?: number | null;
  legacy_completion_incomplete: boolean;
  created_at: string;
  updated_at: string;
  recipient_name?: string | null;
  donor_name?: string | null;
  donor_phone?: string | null;
  distance_km?: number | null;
}

export interface CreateBloodRequestData {
  patient_name: string;
  blood_group: string;
  units: number;
  hospital_name: string;
  hospital_address?: string;
  latitude: number;
  longitude: number;
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
export interface DonationCommitment {
  id: number;
  request_id: number;
  donor_id: number | null;
  status: CommitmentStatus;
  donor_name?: string | null;
  donor_phone?: string | null;
  committed_at: string;
  completed_at?: string | null;
}
export interface ActiveDonation {
  commitment: DonationCommitment;
  request: BloodRequest;
}

export async function createBloodRequest(data: CreateBloodRequestData) {
  const response = await api.post<BloodRequest>('/blood-requests', data);
  return response.data;
}
export async function getBloodRequest(id: number) {
  return (await api.get<BloodRequest>(`/blood-requests/${id}`)).data;
}
export async function acceptRequest(id: number) {
  return (await api.post<BloodRequest>(`/blood-requests/${id}/accept`)).data;
}
export async function withdrawDonation(id: number) {
  return (await api.post<BloodRequest>(`/blood-requests/${id}/withdraw`)).data;
}
export async function completeRequest(id: number) {
  // Only valid after *every* unit has been confirmed by the recipient.
  return (await api.post<BloodRequest>(`/blood-requests/${id}/complete`)).data;
}
export async function cancelRequest(id: number) {
  return (await api.post<BloodRequest>(`/blood-requests/${id}/cancel`)).data;
}
export async function getRequestCommitments(id: number) {
  return (await api.get<DonationCommitment[]>(`/blood-requests/${id}/commitments`)).data;
}
export async function confirmDonation(id: number, commitmentId: number) {
  return (await api.post<BloodRequest>(
    `/blood-requests/${id}/commitments/${commitmentId}/confirm`)).data;
}
export async function releaseDonation(id: number, commitmentId: number) {
  return (await api.post<BloodRequest>(
    `/blood-requests/${id}/commitments/${commitmentId}/release`)).data;
}
export async function getNearbyRequests(params: {
  latitude: number; longitude: number; radius_km?: number; limit?: number; offset?: number;
}) {
  return (await api.get<PaginatedResponse<BloodRequest>>('/blood-requests/nearby', {params})).data;
}
export async function getMyRequests(params?: {limit?: number; offset?: number}) {
  return (await api.get<PaginatedResponse<BloodRequest>>('/blood-requests/mine', {params})).data;
}
export async function getMyActiveDonations(params?: {limit?: number; offset?: number}) {
  return (await api.get<PaginatedResponse<ActiveDonation>>(
    '/blood-requests/commitments/mine', {params})).data;
}
