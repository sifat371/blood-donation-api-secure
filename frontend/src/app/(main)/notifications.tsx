/**
 * Notifications screen — list of notifications with read/unread state.
 *
 * Tapping a notification opens the blood request it refers to; every
 * notification the backend sends carries a `request_id` in its payload.
 */

import { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  RefreshControl,
} from 'react-native';
import { useFocusEffect } from 'expo-router';
import { classifyError } from '@/api/errors';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import {
  getNotifications,
  markNotificationRead,
  openNotificationTarget,
  AppNotification,
} from '@/api/notifications';

const TYPE_EMOJIS: Record<string, string> = {
  'New Blood Request': '🩸',
  'Accepted Request': '✅',
  'Cancelled Request': '❌',
  'Request Completed': '🎉',
  'Profile Reminder': '👤',
  'Donation Eligible Reminder': '💉',
};

/**
 * The backend stamps rows with `datetime.utcnow()`, which serialises without a
 * timezone designator. JavaScript reads a bare date-time as *local* time, so in
 * Bangladesh (UTC+6) every notification looked six hours in the future and the
 * relative time came out negative. Treat an unmarked timestamp as UTC, which is
 * what it is.
 */
function parseServerDate(value: string): Date {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

function describeError(err: unknown): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR'
    ? 'Could not load notifications.'
    : failure.message;
}

export default function NotificationsScreen() {
  const colors = useThemeColors();
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Reference point for the "5m ago" labels, captured when the list arrives
  // rather than read during render. `reactCompiler` is enabled, so a memoized
  // row that called Date.now() itself could keep rendering a frozen timestamp.
  // Refreshing it alongside the data also means every row in one paint is
  // measured from the same instant.
  const [nowMs, setNowMs] = useState(0);

  const fetchNotifications = async () => {
    setLoading(true);
    setError('');
    try {
      const result = await getNotifications();
      setNowMs(Date.now());
      setNotifications(result.items);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  };

  useFocusEffect(
    useCallback(() => {
      fetchNotifications();
    }, [])
  );

  const handleMarkRead = async (id: number) => {
    // Optimistic: the dot disappearing is the whole point of the tap, and the
    // navigation below happens immediately either way.
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, is_read: true } : n))
    );
    try {
      await markNotificationRead(id);
    } catch (e) {
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, is_read: false } : n))
      );
    }
  };

  const handlePress = (item: AppNotification) => {
    if (!item.is_read) {
      handleMarkRead(item.id);
    }
    openNotificationTarget(item.data);
  };

  const formatTime = (dateStr: string) => {
    const date = parseServerDate(dateStr);
    const diffMs = nowMs - date.getTime();
    // Clock skew between device and server can make a fresh row look future-dated.
    const diffMins = Math.max(0, Math.floor(diffMs / 60000));
    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  };

  const renderNotification = ({ item }: { item: AppNotification }) => (
    <Pressable
      style={[
        styles.notificationCard,
        {
          backgroundColor: item.is_read ? colors.surface : colors.primarySurface,
          borderColor: item.is_read ? colors.border : colors.primary + '30',
        },
      ]}
      onPress={() => handlePress(item)}
    >
      <View style={styles.notificationRow}>
        <Text style={styles.typeEmoji}>{TYPE_EMOJIS[item.type] || '📌'}</Text>
        <View style={{ flex: 1 }}>
          <View style={styles.titleRow}>
            <Text style={[styles.notificationTitle, { color: colors.text }]} numberOfLines={1}>
              {item.title}
            </Text>
            {!item.is_read && <View style={[styles.unreadDot, { backgroundColor: colors.primary }]} />}
          </View>
          <Text style={[styles.notificationBody, { color: colors.textSecondary }]} numberOfLines={2}>
            {item.body}
          </Text>
          <Text style={[styles.notificationTime, { color: colors.textTertiary }]}>
            {formatTime(item.created_at)}
          </Text>
        </View>
      </View>
    </Pressable>
  );

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <View style={[styles.headerSection, { backgroundColor: colors.surface, borderBottomColor: colors.border }]}>
        <Text style={[styles.title, { color: colors.text }]}>Notifications</Text>
        <Text style={[styles.countText, { color: colors.textSecondary }]}>
          {notifications.filter((n) => !n.is_read).length} unread
        </Text>
      </View>

      <FlatList
        data={notifications}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderNotification}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={fetchNotifications} tintColor={colors.primary} />}
        contentContainerStyle={styles.listContent}
        ListEmptyComponent={
          loading ? null : error ? (
            <View style={styles.emptyState}>
              <Text style={styles.emptyEmoji}>⚠️</Text>
              <Text style={[styles.emptyTitle, { color: colors.text }]}>
                Something went wrong
              </Text>
              <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
                {error}
              </Text>
              <Pressable
                style={[styles.emptyAction, { backgroundColor: colors.primary }]}
                onPress={fetchNotifications}
              >
                <Text style={[styles.emptyActionText, { color: colors.textOnPrimary }]}>
                  Try Again
                </Text>
              </Pressable>
            </View>
          ) : (
            <View style={styles.emptyState}>
              <Text style={styles.emptyEmoji}>🔕</Text>
              <Text style={[styles.emptyTitle, { color: colors.text }]}>No notifications</Text>
              <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
                You&apos;ll see alerts for blood requests here
              </Text>
            </View>
          )
        }
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
  title: {
    fontSize: Typography.sizes.xl,
    fontWeight: '800',
  },
  countText: {
    fontSize: Typography.sizes.sm,
    marginTop: Spacing.xs,
  },
  listContent: {
    padding: Spacing.xl,
    gap: Spacing.sm,
    paddingBottom: 100,
  },
  notificationCard: {
    borderRadius: Radius.lg,
    padding: Spacing.base,
    borderWidth: 1,
  },
  notificationRow: {
    flexDirection: 'row',
    gap: Spacing.md,
  },
  typeEmoji: {
    fontSize: 24,
    marginTop: 2,
  },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
  },
  unreadDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  notificationTitle: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
    flex: 1,
  },
  notificationBody: {
    fontSize: Typography.sizes.sm,
    marginTop: 4,
    lineHeight: 20,
  },
  notificationTime: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.sm,
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
