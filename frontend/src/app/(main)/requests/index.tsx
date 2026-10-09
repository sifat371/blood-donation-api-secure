/**
 * Requests screen — two views behind one toggle.
 *
 * "Mine" is the requester's side: the requests you created, with the actions
 * only an owner can take (confirm received, cancel).
 *
 * "Nearby" is the donor's side: open requests near you that you could answer.
 * This view is what makes the donor half of the app reachable — before it
 * existed, the request detail screen had no entry point, so nobody could
 * actually accept a request from the UI.
 *
 * Every card opens the detail screen, which is where accepting happens.
 */

import { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  RefreshControl,
  Alert,
} from 'react-native';
import { useRouter, useFocusEffect } from 'expo-router';
import { classifyError } from '@/api/errors';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import {
  getMyRequests,
  getNearbyRequests,
  BloodRequest,
  completeRequest,
  cancelRequest,
} from '@/api/blood-requests';
import { useAuthStore } from '@/store/auth-store';
import { useLocationStore } from '@/store/location-store';
import { getLocationPermission } from '@/api/permission-startup';

const STATUS_EMOJIS: Record<string, string> = {
  Pending: '⏳',
  Accepted: '✅',
  Completed: '🎉',
  Cancelled: '❌',
};

/**
 * Search radius for the nearby feed. The backend accepts 1–100 km and defaults
 * to 20; 50 is a better fit for a donor who is willing to travel, and it is
 * shown in the UI so the scope of the list is never a mystery.
 */
const NEARBY_RADIUS_KM = 50;

type Mode = 'mine' | 'nearby';

/** Turn a failed load into a sentence the user can act on. */
function describeError(err: unknown): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR'
    ? 'Could not load requests.'
    : failure.message;
}

function describeActionError(err: unknown, fallback: string): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR' ? fallback : failure.message;
}

export default function RequestsScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const gpsLatitude = useLocationStore((s) => s.latitude);
  const gpsLongitude = useLocationStore((s) => s.longitude);

  const [mode, setMode] = useState<Mode>('mine');
  const [requests, setRequests] = useState<BloodRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [needsLocation, setNeedsLocation] = useState(false);

  /**
   * Live GPS first, the coordinates saved on the profile second. Deliberately
   * no hardcoded city fallback: showing a Dhaka donor's feed to someone in
   * Sylhet is worse than telling them their location is missing.
   */
  // Memoized so `fetchRequests` can depend on it directly. The alternative —
  // repeating these four values in `fetchRequests`'s own dependency list — works
  // only until someone reads a fifth value here, at which point that list is
  // silently stale and Nearby searches from the old position.
  const resolveCoordinates = useCallback(() => {
    const latitude = gpsLatitude ?? user?.latitude ?? null;
    const longitude = gpsLongitude ?? user?.longitude ?? null;
    if (latitude == null || longitude == null) return null;
    return { latitude, longitude };
  }, [gpsLatitude, gpsLongitude, user?.latitude, user?.longitude]);

  const fetchRequests = useCallback(
    async (which: Mode) => {
      setLoading(true);
      setError('');
      setNeedsLocation(false);
      try {
        if (which === 'mine') {
          const result = await getMyRequests();
          setRequests(result.items);
          setTotal(result.total);
        } else {
          const coords = resolveCoordinates();
          if (!coords) {
            setRequests([]);
            setTotal(0);
            setNeedsLocation(true);
            return;
          }
          const result = await getNearbyRequests({
            ...coords,
            radius_km: NEARBY_RADIUS_KM,
          });
          setRequests(result.items);
          setTotal(result.total);
        }
      } catch (e) {
        setRequests([]);
        setTotal(0);
        setError(describeError(e));
      } finally {
        setLoading(false);
      }
    },
    // Re-created when the coordinates change so switching to Nearby right after
    // a GPS fix uses the new position.
    [resolveCoordinates]
  );

  useFocusEffect(
    useCallback(() => {
      fetchRequests(mode);
    }, [mode, fetchRequests])
  );

  const requestLocation = async () => {
    setLoading(true);
    try {
      await getLocationPermission();
    } catch (e) {
      setError('Could not read your location. Check that GPS is enabled.');
    } finally {
      setLoading(false);
    }
    // The store update re-runs fetchRequests via the effect above; call it here
    // too so a denied permission still clears the spinner into a real message.
    fetchRequests('nearby');
  };

  const openRequest = (id: number) => {
    router.push({
      pathname: '/(main)/requests/[id]',
      params: { id: String(id) },
    } as any);
  };

  const handleComplete = async (id: number) => {
    Alert.alert('Confirm Donation', 'Have you received the blood donation?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Yes, Confirm',
        onPress: async () => {
          try {
            await completeRequest(id);
            fetchRequests(mode);
          } catch (e) {
            Alert.alert('Error', describeActionError(e, 'Failed to complete'));
          }
        },
      },
    ]);
  };

  const handleCancel = async (id: number) => {
    Alert.alert('Cancel Request', 'Are you sure you want to cancel this request?', [
      { text: 'No', style: 'cancel' },
      {
        text: 'Yes, Cancel',
        style: 'destructive',
        onPress: async () => {
          try {
            await cancelRequest(id);
            fetchRequests(mode);
          } catch (e) {
            Alert.alert('Error', describeActionError(e, 'Failed to cancel'));
          }
        },
      },
    ]);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'Pending': return colors.pending;
      case 'Accepted': return colors.accepted;
      case 'Completed': return colors.completed;
      case 'Cancelled': return colors.cancelled;
      default: return colors.textSecondary;
    }
  };

  const renderRequest = ({ item }: { item: BloodRequest }) => {
    // In "Mine" every row is owned by the viewer; in "Nearby" the backend
    // excludes the viewer's own requests, so none of them are.
    const isMine = mode === 'mine';

    return (
      <Pressable
        onPress={() => openRequest(item.id)}
        style={({ pressed }) => [
          styles.requestCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.border,
            opacity: pressed ? 0.75 : 1,
          },
        ]}
      >
        <View style={styles.requestHeader}>
          <View style={[styles.statusBadge, { backgroundColor: getStatusColor(item.status) + '20' }]}>
            <Text style={styles.statusEmoji}>{STATUS_EMOJIS[item.status] || '❓'}</Text>
            <Text style={[styles.statusText, { color: getStatusColor(item.status) }]}>{item.status}</Text>
          </View>
          <View style={[styles.bloodBadge, { backgroundColor: colors.bloodBadge }]}>
            <Text style={[styles.bloodText, { color: colors.bloodBadgeText }]}>{item.blood_group}</Text>
          </View>
        </View>

        <Text style={[styles.patientName, { color: colors.text }]}>{item.patient_name}</Text>
        <Text style={[styles.hospitalName, { color: colors.textSecondary }]}>
          🏥 {item.hospital_name}
        </Text>
        <Text style={[styles.requestDetail, { color: colors.textTertiary }]}>
          {item.units} unit(s) • Needed: {item.needed_date}
          {item.distance_km != null ? ` • ${item.distance_km.toFixed(1)} km away` : ''}
        </Text>

        {item.donor_name && (
          <View style={[styles.donorInfo, { backgroundColor: colors.successLight }]}>
            <Text style={[styles.donorInfoText, { color: colors.success }]}>
              Accepted by: {item.donor_name} {item.donor_phone ? `(${item.donor_phone})` : ''}
            </Text>
          </View>
        )}

        {isMine ? (
          <View style={styles.actionRow}>
            {item.status === 'Accepted' && (
              <Pressable
                style={[styles.actionButton, { backgroundColor: colors.success }]}
                onPress={() => handleComplete(item.id)}
              >
                <Text style={styles.actionButtonText}>Confirm Received</Text>
              </Pressable>
            )}
            {(item.status === 'Pending' || item.status === 'Accepted') && (
              <Pressable
                style={[styles.actionButton, { backgroundColor: colors.error + '20', borderColor: colors.error, borderWidth: 1 }]}
                onPress={() => handleCancel(item.id)}
              >
                <Text style={[styles.actionButtonText, { color: colors.error }]}>Cancel</Text>
              </Pressable>
            )}
          </View>
        ) : (
          <Text style={[styles.tapHint, { color: colors.primary }]}>
            {item.status === 'Pending' ? 'Tap to view and accept →' : 'Tap to view →'}
          </Text>
        )}
      </Pressable>
    );
  };

  const renderEmpty = () => {
    if (loading) return null;

    if (needsLocation) {
      return (
        <View style={styles.emptyState}>
          <Text style={styles.emptyEmoji}>📍</Text>
          <Text style={[styles.emptyTitle, { color: colors.text }]}>Location needed</Text>
          <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
            Nearby requests are matched to where you are. Share your location to
            see requests you could answer.
          </Text>
          <Pressable
            style={[styles.emptyAction, { backgroundColor: colors.primary }]}
            onPress={requestLocation}
          >
            <Text style={[styles.emptyActionText, { color: colors.textOnPrimary }]}>
              Use My Location
            </Text>
          </Pressable>
        </View>
      );
    }

    if (error) {
      return (
        <View style={styles.emptyState}>
          <Text style={styles.emptyEmoji}>⚠️</Text>
          <Text style={[styles.emptyTitle, { color: colors.text }]}>Something went wrong</Text>
          <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>{error}</Text>
          <Pressable
            style={[styles.emptyAction, { backgroundColor: colors.primary }]}
            onPress={() => fetchRequests(mode)}
          >
            <Text style={[styles.emptyActionText, { color: colors.textOnPrimary }]}>
              Try Again
            </Text>
          </Pressable>
        </View>
      );
    }

    return (
      <View style={styles.emptyState}>
        <Text style={styles.emptyEmoji}>{mode === 'mine' ? '📋' : '🔍'}</Text>
        <Text style={[styles.emptyTitle, { color: colors.text }]}>
          {mode === 'mine' ? 'No requests yet' : 'No nearby requests'}
        </Text>
        <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
          {mode === 'mine'
            ? 'Create a blood request when you need it'
            : `Nobody within ${NEARBY_RADIUS_KM} km needs blood right now. Pull down to refresh.`}
        </Text>
      </View>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      {/* Header */}
      <View style={[styles.headerSection, { backgroundColor: colors.surface, borderBottomColor: colors.border }]}>
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: colors.text }]}>Requests</Text>
          <Pressable
            style={[styles.createButton, { backgroundColor: colors.primary }]}
            onPress={() => router.push('/(main)/requests/create' as any)}
          >
            <Text style={[styles.createButtonText, { color: colors.textOnPrimary }]}>+ New</Text>
          </Pressable>
        </View>

        <View style={[styles.toggleRow, { backgroundColor: colors.surfaceVariant, borderColor: colors.border }]}>
          {(['mine', 'nearby'] as Mode[]).map((option) => (
            <Pressable
              key={option}
              onPress={() => setMode(option)}
              style={[
                styles.toggleButton,
                mode === option && { backgroundColor: colors.primary },
              ]}
            >
              <Text
                style={[
                  styles.toggleText,
                  { color: mode === option ? colors.textOnPrimary : colors.textSecondary },
                ]}
              >
                {option === 'mine' ? 'My Requests' : 'Nearby'}
              </Text>
            </Pressable>
          ))}
        </View>

        <Text style={[styles.countText, { color: colors.textSecondary }]}>
          {mode === 'mine'
            ? `${total} request(s)`
            : `${total} request(s) within ${NEARBY_RADIUS_KM} km`}
        </Text>
      </View>

      {/* An error while rows are already on screen would otherwise be invisible,
          because ListEmptyComponent only renders for an empty list. */}
      {error && requests.length > 0 ? (
        <Text style={[styles.errorBanner, { color: colors.error, backgroundColor: colors.errorLight }]}>
          {error}
        </Text>
      ) : null}

      <FlatList
        data={requests}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderRequest}
        refreshControl={
          <RefreshControl
            refreshing={loading}
            onRefresh={() => fetchRequests(mode)}
            tintColor={colors.primary}
          />
        }
        contentContainerStyle={styles.listContent}
        ListEmptyComponent={renderEmpty}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  headerSection: {
    padding: Spacing.xl,
    paddingTop: 60,
    borderBottomWidth: 1,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: {
    fontSize: Typography.sizes.xl,
    fontWeight: '800',
  },
  createButton: {
    paddingHorizontal: Spacing.base,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.md,
  },
  createButtonText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  toggleRow: {
    flexDirection: 'row',
    marginTop: Spacing.base,
    padding: 3,
    borderRadius: Radius.full,
    borderWidth: 1,
  },
  toggleButton: {
    flex: 1,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.full,
    alignItems: 'center',
  },
  toggleText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  countText: {
    fontSize: Typography.sizes.sm,
    marginTop: Spacing.sm,
  },
  errorBanner: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.xl,
  },
  listContent: {
    padding: Spacing.xl,
    gap: Spacing.md,
    paddingBottom: 100,
  },
  requestCard: {
    borderRadius: Radius.lg,
    padding: Spacing.base,
    borderWidth: 1,
  },
  requestHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.sm,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs,
    borderRadius: Radius.full,
    gap: 4,
  },
  statusEmoji: {
    fontSize: 14,
  },
  statusText: {
    fontSize: Typography.sizes.xs,
    fontWeight: '700',
  },
  bloodBadge: {
    width: 36,
    height: 36,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center',
  },
  bloodText: {
    fontSize: Typography.sizes.xs,
    fontWeight: '800',
  },
  patientName: {
    fontSize: Typography.sizes.md,
    fontWeight: '700',
  },
  hospitalName: {
    fontSize: Typography.sizes.sm,
    marginTop: 4,
  },
  requestDetail: {
    fontSize: Typography.sizes.xs,
    marginTop: 4,
  },
  tapHint: {
    fontSize: Typography.sizes.xs,
    fontWeight: '700',
    marginTop: Spacing.md,
  },
  donorInfo: {
    marginTop: Spacing.md,
    padding: Spacing.md,
    borderRadius: Radius.md,
  },
  donorInfoText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
  },
  actionRow: {
    flexDirection: 'row',
    gap: Spacing.sm,
    marginTop: Spacing.md,
  },
  actionButton: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: Radius.md,
    alignItems: 'center',
  },
  actionButtonText: {
    color: '#FFF',
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  emptyState: {
    alignItems: 'center',
    paddingVertical: Spacing.xxxl,
  },
  emptyEmoji: {
    fontSize: 48,
    marginBottom: Spacing.base,
  },
  emptyTitle: {
    fontSize: Typography.sizes.md,
    fontWeight: '700',
  },
  emptySubtitle: {
    fontSize: Typography.sizes.sm,
    marginTop: Spacing.sm,
    textAlign: 'center',
  },
  emptyAction: {
    marginTop: Spacing.lg,
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.md,
    borderRadius: Radius.md,
  },
  emptyActionText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
});
