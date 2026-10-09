/**
 * Home screen — dashboard with availability toggle, quick actions,
 * and eligibility stats.
 */

import { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  Switch,
  RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { classifyError } from '@/api/errors';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { useAuthStore } from '@/store/auth-store';
import { getMyProfile, updateProfile } from '@/api/profile';

/** Mirrors `ELIGIBILITY_DAYS` in the backend's app/services/eligibility.py. */
const ELIGIBILITY_DAYS = 90;

function describeError(err: unknown, fallback: string): string {
  // The shared classifier already distinguishes an unreachable backend from a
  // rejected request, a rate limit and a server fault. The caller's fallback is
  // only reached when nothing recognisable came back at all.
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR' ? fallback : failure.message;
}

/**
 * Whole days since a `YYYY-MM-DD` donation date, or null if there isn't one.
 *
 * Compared at local midnight on both sides so the answer doesn't drift by one
 * depending on the time of day.
 */
function daysSince(dateString?: string | null): number | null {
  if (!dateString) return null;
  const then = new Date(`${dateString}T00:00:00`);
  if (Number.isNaN(then.getTime())) return null;
  const today = new Date();
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(0, Math.round((midnight.getTime() - then.getTime()) / 86_400_000));
}

export default function HomeScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [savingAvailability, setSavingAvailability] = useState(false);

  const isAvailable = !!user?.is_available;

  // The same rule the backend applies when deciding who to notify, so the card
  // can't claim someone is eligible when a search would skip them. It used to
  // print a hardcoded "90+" for anyone with any donation on record and label
  // availability as "Eligible" — a donor who gave blood last week was told they
  // were good to go.
  const sinceLastDonation = daysSince(user?.last_donation_date);
  const restedLongEnough =
    sinceLastDonation === null || sinceLastDonation >= ELIGIBILITY_DAYS;
  const isEligible = isAvailable && restedLongEnough;

  const handleAvailabilityToggle = async (value: boolean) => {
    if (!user) return;
    setSavingAvailability(true);
    setError('');
    // Optimistic, then reconciled against the server's copy.
    setUser({ ...user, is_available: value });
    try {
      const updated = await updateProfile({ is_available: value });
      setUser(updated);
    } catch (e) {
      setUser({ ...user, is_available: !value });
      setError(describeError(e, 'Could not update your availability.'));
    } finally {
      setSavingAvailability(false);
    }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    setError('');
    try {
      setUser(await getMyProfile());
    } catch (e) {
      setError(describeError(e, 'Could not refresh your profile.'));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.background }}
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
    >
      {/* Header */}
      <View style={styles.header}>
        <View>
          <Text style={[styles.greeting, { color: colors.textSecondary }]}>Welcome back,</Text>
          <Text style={[styles.userName, { color: colors.text }]}>{user?.name || 'User'}</Text>
        </View>
        <View style={[styles.bloodBadge, { backgroundColor: colors.bloodBadge }]}>
          <Text style={[styles.bloodBadgeText, { color: colors.bloodBadgeText }]}>
            {user?.blood_group || '?'}
          </Text>
        </View>
      </View>

      {error ? (
        <Text
          style={[
            styles.errorBanner,
            { color: colors.error, backgroundColor: colors.errorLight },
          ]}
        >
          {error}
        </Text>
      ) : null}

      {/* Availability Toggle Card */}
      <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <View style={styles.cardRow}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.cardTitle, { color: colors.text }]}>Availability</Text>
            <Text style={[styles.cardSubtitle, { color: colors.textSecondary }]}>
              {isAvailable
                ? 'You are visible to blood requests'
                : 'You are hidden from blood requests'}
            </Text>
          </View>
          <Switch
            value={isAvailable}
            onValueChange={handleAvailabilityToggle}
            disabled={savingAvailability || !user}
            trackColor={{ false: colors.surfaceVariant, true: colors.success + '40' }}
            thumbColor={isAvailable ? colors.success : colors.textTertiary}
          />
        </View>
      </View>

      {/* Emergency Request Button */}
      <Pressable
        style={[styles.emergencyButton, { backgroundColor: colors.primary }]}
        onPress={() => router.push('/(main)/requests/create' as any)}
      >
        <Text style={styles.emergencyEmoji}>🚨</Text>
        <View>
          <Text style={[styles.emergencyTitle, { color: colors.textOnPrimary }]}>
            Emergency Blood Request
          </Text>
          <Text style={[styles.emergencySubtitle, { color: colors.textOnPrimary + 'CC' }]}>
            Request blood donors near you
          </Text>
        </View>
      </Pressable>

      {/* Quick Actions */}
      <Text style={[styles.sectionTitle, { color: colors.text }]}>Quick Actions</Text>
      <View style={styles.quickActions}>
        <Pressable
          style={[styles.actionCard, { backgroundColor: colors.surface, borderColor: colors.border }]}
          onPress={() => router.push('/(main)/find-donor')}
        >
          <Text style={styles.actionEmoji}>🔍</Text>
          <Text style={[styles.actionText, { color: colors.text }]}>Find Donor</Text>
        </Pressable>
        <Pressable
          style={[styles.actionCard, { backgroundColor: colors.surface, borderColor: colors.border }]}
          onPress={() => router.push('/(main)/requests')}
        >
          <Text style={styles.actionEmoji}>📋</Text>
          <Text style={[styles.actionText, { color: colors.text }]}>My Requests</Text>
        </Pressable>
        <Pressable
          style={[styles.actionCard, { backgroundColor: colors.surface, borderColor: colors.border }]}
          onPress={() => router.push('/(main)/chat')}
        >
          <Text style={styles.actionEmoji}>💬</Text>
          <Text style={[styles.actionText, { color: colors.text }]}>AI Assistant</Text>
        </Pressable>
        <Pressable
          style={[styles.actionCard, { backgroundColor: colors.surface, borderColor: colors.border }]}
          onPress={() => router.push('/(main)/notifications')}
        >
          <Text style={styles.actionEmoji}>🔔</Text>
          <Text style={[styles.actionText, { color: colors.text }]}>Notifications</Text>
        </Pressable>
      </View>

      {/* Stats Card */}
      <View style={[styles.statsCard, { backgroundColor: colors.primaryLight }]}>
        <Text style={[styles.statsTitle, { color: colors.primary }]}>Donation Stats</Text>
        <View style={styles.statsRow}>
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: colors.primary }]}>
              {sinceLastDonation === null ? '—' : sinceLastDonation}
            </Text>
            <Text style={[styles.statLabel, { color: colors.textSecondary }]}>
              {sinceLastDonation === null ? 'Never Donated' : 'Days Since'}
            </Text>
          </View>
          <View style={[styles.statDivider, { backgroundColor: colors.primary + '30' }]} />
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: colors.primary }]}>
              {isEligible ? 'Yes' : 'No'}
            </Text>
            <Text style={[styles.statLabel, { color: colors.textSecondary }]}>Eligible</Text>
          </View>
          <View style={[styles.statDivider, { backgroundColor: colors.primary + '30' }]} />
          <View style={styles.statItem}>
            <Text style={[styles.statValue, { color: colors.primary }]}>
              {user?.blood_group || '?'}
            </Text>
            <Text style={[styles.statLabel, { color: colors.textSecondary }]}>Blood Type</Text>
          </View>
        </View>

        {/* Spell out why, when the answer is no — "No" alone leaves a willing
            donor with nothing to act on. */}
        {!isEligible && (
          <Text style={[styles.statsNote, { color: colors.textSecondary }]}>
            {!restedLongEnough && sinceLastDonation !== null
              ? `You can donate again in ${ELIGIBILITY_DAYS - sinceLastDonation} day${
                  ELIGIBILITY_DAYS - sinceLastDonation === 1 ? '' : 's'
                } — donations are ${ELIGIBILITY_DAYS} days apart.`
              : 'Turn on availability above to appear in donor searches.'}
          </Text>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: Spacing.xl,
    paddingTop: 60,
    paddingBottom: 100,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.xl,
  },
  greeting: {
    fontSize: Typography.sizes.base,
  },
  userName: {
    fontSize: Typography.sizes.xl,
    fontWeight: '800',
  },
  bloodBadge: {
    width: 52,
    height: 52,
    borderRadius: 26,
    justifyContent: 'center',
    alignItems: 'center',
  },
  bloodBadgeText: {
    fontSize: Typography.sizes.md,
    fontWeight: '800',
  },
  card: {
    borderRadius: Radius.lg,
    padding: Spacing.base,
    borderWidth: 1,
    marginBottom: Spacing.base,
  },
  cardRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  cardTitle: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
  },
  cardSubtitle: {
    fontSize: Typography.sizes.sm,
    marginTop: 2,
  },
  emergencyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: Spacing.lg,
    borderRadius: Radius.lg,
    gap: Spacing.base,
    marginBottom: Spacing.xl,
  },
  emergencyEmoji: {
    fontSize: 32,
  },
  emergencyTitle: {
    fontSize: Typography.sizes.md,
    fontWeight: '800',
  },
  emergencySubtitle: {
    fontSize: Typography.sizes.sm,
    marginTop: 2,
  },
  sectionTitle: {
    fontSize: Typography.sizes.lg,
    fontWeight: '700',
    marginBottom: Spacing.base,
  },
  quickActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.md,
    marginBottom: Spacing.xl,
  },
  actionCard: {
    width: '47%',
    padding: Spacing.base,
    borderRadius: Radius.lg,
    borderWidth: 1,
    alignItems: 'center',
    gap: Spacing.sm,
  },
  actionEmoji: {
    fontSize: 28,
  },
  actionText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
  },
  statsCard: {
    borderRadius: Radius.lg,
    padding: Spacing.lg,
  },
  statsTitle: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
    marginBottom: Spacing.base,
  },
  statsNote: {
    fontSize: Typography.sizes.xs,
    lineHeight: 18,
    marginTop: Spacing.base,
    textAlign: 'center',
  },
  errorBanner: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
    padding: Spacing.md,
    borderRadius: Radius.md,
    marginBottom: Spacing.base,
    textAlign: 'center',
  },
  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  statItem: {
    flex: 1,
    alignItems: 'center',
  },
  statValue: {
    fontSize: Typography.sizes.xl,
    fontWeight: '800',
  },
  statLabel: {
    fontSize: Typography.sizes.xs,
    marginTop: 4,
  },
  statDivider: {
    width: 1,
    height: 40,
  },
});
