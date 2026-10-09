/**
 * Notifications API functions, plus the shared rule for turning a notification
 * into a screen.
 */

import { router } from 'expo-router';
import api from './client';
import { PaginatedResponse } from './blood-requests';

export interface AppNotification {
  id: number;
  user_id: number;
  type: string;
  title: string;
  body: string;
  /** The REST API returns an object or null. */
  data?: Record<string, unknown> | null;
  is_read: boolean;
  created_at: string;
}

/**
 * Pull `request_id` out of a notification payload.
 *
 * The same logical payload reaches the app in three shapes: a JSON string on
 * rows from `GET /notifications`, a flat string→string map from FCM (data
 * payloads must be strings on the wire), and an already-parsed object from
 * expo-notifications. Every notification the backend sends includes
 * `request_id`, so one parser serves all the deep links.
 */
export function extractRequestId(data: unknown): number | null {
  let payload: unknown = data;

  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      return null;
    }
  }

  if (!payload || typeof payload !== 'object') return null;

  const raw = (payload as Record<string, unknown>).request_id;
  const id = typeof raw === 'string' ? Number(raw) : raw;
  return typeof id === 'number' && Number.isInteger(id) && id > 0 ? id : null;
}

/**
 * Navigate to whatever a notification refers to. Returns false when the payload
 * carries no destination, so callers can fall back to the notifications list.
 *
 * Uses the imperative router because this also runs from notification handlers
 * registered outside the React tree.
 */
export function openNotificationTarget(data: unknown): boolean {
  const requestId = extractRequestId(data);
  if (requestId == null) return false;

  router.push({
    pathname: '/(main)/requests/[id]',
    params: { id: String(requestId) },
  } as any);
  return true;
}

export async function getNotifications(params?: {
  is_read?: boolean;
  limit?: number;
  offset?: number;
}) {
  const { data } = await api.get<PaginatedResponse<AppNotification>>('/notifications', { params });
  return data;
}

export async function markNotificationRead(notificationId: number) {
  const { data } = await api.post<AppNotification>(`/notifications/${notificationId}/read`);
  return data;
}
