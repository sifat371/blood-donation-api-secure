/**
 * Request detail screen — view status, donor info, and manage request.
 *
 * Reached from the My/Nearby lists on the requests tab and from tapping a push
 * notification. For a donor this is where accepting happens.
 */

import { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useLocalSearchParams, useRouter, useFocusEffect } from 'expo-router';
import { classifyError } from '@/api/errors';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { useAuthStore } from '@/store/auth-store';
import {
  BloodRequest,
  getBloodRequest,
  cancelRequest,
  acceptRequest,
  withdrawDonation,
  confirmDonation,
  releaseDonation,
  getRequestCommitments,
  DonationCommitment,
} from '@/api/blood-requests';
import { StatusBadge } from '../../../components/StatusBadge';
import { MapView } from '@/components/MapView';

/** Distinguish "gone" from "couldn't ask" so the user knows whether to retry. */
type LoadError =
  | { kind: 'not-found' }
  // Carries its own message so the screen can say *which* network problem it
  // was — no server on this Wi-Fi, no Wi-Fi at all, or a timeout.
  | { kind: 'network'; message: string }
  | { kind: 'other'; message: string };

function describeLoadError(err: unknown): LoadError {
  const failure = classifyError(err);
  if (failure.kind === 'NOT_FOUND') return { kind: 'not-found' };
  if (
    failure.kind === 'NETWORK_UNREACHABLE' ||
    failure.kind === 'OFFLINE' ||
    failure.kind === 'TIMEOUT'
  ) {
    return { kind: 'network', message: failure.message };
  }
  return {
    kind: 'other',
    message:
      failure.kind === 'UNKNOWN_ERROR'
        ? 'Could not load this request.'
        : failure.message,
  };
}

function describeActionError(err: unknown, action: string): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR'
    ? `Failed to ${action} request.`
    : failure.message;
}

export default function RequestDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const colors = useThemeColors();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const requestId = Number(id);

  const [request, setRequest] = useState<BloodRequest | null>(null);
  const [commitments, setCommitments] = useState<DonationCommitment[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<LoadError | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const fetchRequest = useCallback(async () => {
    if (!Number.isFinite(requestId)) {
      setLoadError({ kind: 'not-found' });
      setLoading(false);
      return;
    }
    try {
      // Uses GET /blood-requests/{id}. This screen used to fetch /mine and then
      // fall back to /nearby, hunting for a matching id — which silently failed
      // for any request that was neither owned by nor near the viewer.
      const data = await getBloodRequest(requestId);
      setRequest(data);
      setLoadError(null);
      if (data.recipient_id === useAuthStore.getState().user?.id) {
        try {
          setCommitments(await getRequestCommitments(requestId));
        } catch (err) {
          console.warn('Could not load donor commitments:', err);
          setCommitments([]);
        }
      } else {
        setCommitments([]);
      }
    } catch (e) {
      setLoadError(describeLoadError(e));
    } finally {
      setLoading(false);
    }
  }, [requestId]);

  useFocusEffect(
    useCallback(() => {
      fetchRequest();
    }, [fetchRequest])
  );

  const isOwner = request?.recipient_id != null && request.recipient_id === user?.id;

  const handleAction = async (action: 'accept' | 'withdraw' | 'cancel') => {
    setActionLoading(true);
    try {
      // Each of these returns the updated request, so render it immediately
      // rather than waiting on a refetch.
      if (action === 'accept') {
        setRequest(await acceptRequest(requestId));
        Alert.alert('Success', 'You have accepted this blood request!');
      } else if (action === 'withdraw') {
        setRequest(await withdrawDonation(requestId));
        Alert.alert('Success', 'Your commitment has been withdrawn.');
      } else {
        setRequest(await cancelRequest(requestId));
        Alert.alert('Success', 'Request cancelled.');
      }
      // Re-read so any server-side enrichment (donor name/phone) shows up.
      fetchRequest();
    } catch (e) {
      Alert.alert('Error', describeActionError(e, action));
      // The failure may be a stale view — e.g. someone else accepted first.
      fetchRequest();
    } finally {
      setActionLoading(false);
    }
  };

  const handleCommitmentAction = (action: 'confirm' | 'release', commitmentId: number) => {
    Alert.alert(
      action === 'confirm' ? 'Confirm donated blood' : 'Release donor',
      action === 'confirm'
        ? 'Confirm this donor actually donated one unit? This cannot be undone.'
        : 'Release this uncompleted commitment and reopen one unit?',
      [
        { text: 'Not now', style: 'cancel' },
        {
          text: action === 'confirm' ? 'Confirm donation' : 'Release',
          style: action === 'release' ? 'destructive' : 'default',
          onPress: async () => {
            setActionLoading(true);
            try {
              if (action === 'confirm') {
                setRequest(await confirmDonation(requestId, commitmentId));
              } else {
                setRequest(await releaseDonation(requestId, commitmentId));
              }
              await fetchRequest();
            } catch (err) {
              Alert.alert('Error', describeActionError(err, action));
              await fetchRequest();
            } finally {
              setActionLoading(false);
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: colors.background }]}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  if (loadError || !request) {
    const message =
      loadError?.kind === 'network'
        ? loadError.message
        : loadError?.kind === 'not-found'
          ? "This request no longer exists, or it isn't available to you."
          : (loadError?.kind === 'other' && loadError.message) ||
            'Request not found';

    return (
      <View style={[styles.center, { backgroundColor: colors.background }]}>
        <Text style={styles.errorEmoji}>
          {loadError?.kind === 'network' ? '📡' : '🔍'}
        </Text>
        <Text style={[styles.errorText, { color: colors.textSecondary }]}>
          {message}
        </Text>
        {loadError?.kind === 'network' && (
          <Pressable
            onPress={() => {
              setLoading(true);
              fetchRequest();
            }}
            style={[styles.retryButton, { backgroundColor: colors.primary }]}
          >
            <Text style={{ color: colors.textOnPrimary, fontWeight: 'bold' }}>
              Try Again
            </Text>
          </Pressable>
        )}
        <Pressable onPress={() => router.back()} style={{ marginTop: 20 }}>
          <Text style={{ color: colors.primary, fontWeight: 'bold' }}>Go Back</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.background }}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} style={styles.backButton}>
          <Text style={{ fontSize: 24, color: colors.text }}>←</Text>
        </Pressable>
        <Text style={[styles.title, { color: colors.text }]}>Request Details</Text>
        <View style={{ width: 24 }} />
      </View>

      <View style={styles.content}>
        <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <View style={styles.rowBetween}>
            <View style={[styles.bloodBadge, { backgroundColor: colors.bloodBadge }]}>
              <Text style={[styles.bloodText, { color: colors.bloodBadgeText }]}>{request.blood_group}</Text>
            </View>
            <StatusBadge status={request.status} />
          </View>

          <Text style={[styles.patientName, { color: colors.text }]}>{request.patient_name || 'Patient details are shared after you commit'}</Text>
          <Text style={[styles.detailText, { color: colors.textSecondary }]}>
            {request.units_required ?? request.units} unit(s) required • {request.units_committed ?? 0} committed • {request.units_completed ?? 0} confirmed • {request.remaining_units ?? request.units} open • Needed {request.needed_date}
          </Text>

          <View style={[styles.divider, { backgroundColor: colors.divider }]} />

          <Text style={[styles.sectionTitle, { color: colors.text }]}>Hospital Information</Text>
          <Text style={[styles.infoText, { color: colors.text }]}>🏥 {request.hospital_name}</Text>
          {request.hospital_address && (
            <Text style={[styles.infoText, { color: colors.textSecondary, marginLeft: 22 }]}>
              {request.hospital_address}
            </Text>
          )}

          {/* Directions. A donor who has accepted needs to get there, and the
              coordinates are already on the request. */}
          {request.latitude != null && request.longitude != null && (
            <View style={{ marginTop: Spacing.md }}>
              <MapView
                markers={[
                  {
                    label: request.hospital_name,
                    latitude: request.latitude,
                    longitude: request.longitude,
                    detail: request.hospital_address,
                  },
                ]}
              />
            </View>
          )}

          <View style={[styles.divider, { backgroundColor: colors.divider }]} />

          <Text style={[styles.sectionTitle, { color: colors.text }]}>Contact Information</Text>
          <Text style={[styles.infoText, { color: colors.text }]}>
            {request.contact_number ? `📞 ${request.contact_number}` : 'Available after a donation commitment'}
          </Text>

          {request.notes && (
            <>
              <View style={[styles.divider, { backgroundColor: colors.divider }]} />
              <Text style={[styles.sectionTitle, { color: colors.text }]}>Notes</Text>
              <Text style={[styles.infoText, { color: colors.textSecondary }]}>{request.notes}</Text>
            </>
          )}
        </View>

        {isOwner && (
          <View style={[styles.donorSection, { backgroundColor: colors.surface, borderColor: colors.border }]}>
            <Text style={[styles.sectionTitle, { color: colors.text }]}>Donor commitments</Text>
            {commitments.length === 0 && (
              <Text style={[styles.infoText, { color: colors.textSecondary }]}>
                No donor commitments yet.
              </Text>
            )}
            {commitments.map((donor) => (
              <View key={donor.id} style={{ paddingVertical: Spacing.sm }}>
                <Text style={[styles.infoText, { color: colors.text }]}>
                  {donor.donor_name || `Donor #${donor.donor_id}`} • {donor.status}
                </Text>
                {donor.donor_phone ? (
                  <Text style={[styles.infoText, { color: colors.textSecondary }]}>
                    📞 {donor.donor_phone}
                  </Text>
                ) : null}
                {donor.status === 'Committed' && (
                  <View style={{ flexDirection: 'row', gap: Spacing.sm, marginTop: Spacing.sm }}>
                    <Pressable
                      accessibilityRole="button"
                      style={[styles.actionButton, { flex: 1, backgroundColor: colors.success }]}
                      disabled={actionLoading}
                      onPress={() => handleCommitmentAction('confirm', donor.id)}>
                      <Text style={styles.actionButtonText}>Confirm 1 unit</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      style={[styles.actionButton, { flex: 1, backgroundColor: colors.error }]}
                      disabled={actionLoading}
                      onPress={() => handleCommitmentAction('release', donor.id)}>
                      <Text style={styles.actionButtonText}>Release</Text>
                    </Pressable>
                  </View>
                )}
              </View>
            ))}
          </View>
        )}

        <View style={styles.actionContainer}>
          {isOwner &&
            ['Pending', 'Partially Committed', 'Fully Committed'].includes(request.status) && (
              <Pressable
                style={[styles.actionButton, styles.cancelButton, { borderColor: colors.error }]}
                onPress={() => handleAction('cancel')}
                disabled={actionLoading}>
                <Text style={[styles.actionButtonText, { color: colors.error }]}>
                  Cancel Request
                </Text>
              </Pressable>
            )}

          {!isOwner && request.my_commitment_status === 'Committed' && (
            <Pressable
              style={[styles.actionButton, { backgroundColor: colors.error }]}
              onPress={() => handleAction('withdraw')}
              disabled={actionLoading}>
              <Text style={styles.actionButtonText}>Withdraw my commitment</Text>
            </Pressable>
          )}
          {!isOwner && request.my_commitment_status === 'Completed' && (
            <Text style={[styles.infoText, { color: colors.success }]}>
              Your donation is confirmed. Thank you!
            </Text>
          )}
          {!isOwner && !['Committed', 'Completed'].includes(request.my_commitment_status || '') &&
            ['Pending', 'Partially Committed'].includes(request.status) &&
            request.remaining_units > 0 && (
              <Pressable
                style={[styles.actionButton, { backgroundColor: colors.primary }]}
                onPress={() => handleAction('accept')}
                disabled={actionLoading}>
                <Text style={styles.actionButtonText}>Commit to donate 1 unit</Text>
              </Pressable>
            )}
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  errorEmoji: {
    fontSize: 48,
    marginBottom: Spacing.base,
  },
  errorText: {
    fontSize: Typography.sizes.base,
    textAlign: 'center',
    paddingHorizontal: Spacing.xl,
  },
  retryButton: {
    marginTop: Spacing.lg,
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.md,
    borderRadius: Radius.md,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.xl,
    paddingTop: 60,
    paddingBottom: Spacing.md,
  },
  backButton: {
    padding: Spacing.xs,
  },
  title: {
    fontSize: Typography.sizes.lg,
    fontWeight: 'bold',
  },
  content: {
    padding: Spacing.xl,
    paddingBottom: 100,
  },
  card: {
    borderRadius: Radius.lg,
    padding: Spacing.lg,
    borderWidth: 1,
    marginBottom: Spacing.lg,
  },
  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.md,
  },
  bloodBadge: {
    width: 44,
    height: 44,
    borderRadius: 22,
    justifyContent: 'center',
    alignItems: 'center',
  },
  bloodText: {
    fontSize: Typography.sizes.md,
    fontWeight: '900',
  },
  patientName: {
    fontSize: Typography.sizes.xl,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  detailText: {
    fontSize: Typography.sizes.sm,
  },
  divider: {
    height: 1,
    marginVertical: Spacing.md,
  },
  sectionTitle: {
    fontSize: Typography.sizes.sm,
    fontWeight: 'bold',
    marginBottom: Spacing.sm,
  },
  infoText: {
    fontSize: Typography.sizes.base,
    marginBottom: 4,
  },
  donorSection: {
    borderRadius: Radius.lg,
    padding: Spacing.lg,
    borderWidth: 1,
    marginBottom: Spacing.lg,
  },
  actionContainer: {
    gap: Spacing.md,
  },
  actionButton: {
    paddingVertical: 16,
    borderRadius: Radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelButton: {
    backgroundColor: 'transparent',
    borderWidth: 1,
  },
  actionButtonText: {
    color: '#FFF',
    fontSize: Typography.sizes.base,
    fontWeight: 'bold',
  },
});
