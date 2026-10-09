/**
 * Profile screen — view/edit profile and donation history.
 *
 * The two controls that actually affect matching — availability and saved
 * coordinates — are one tap each, rather than buried in the edit form. A donor
 * who has just given blood needs to switch off quickly, and a donor who has
 * moved needs their location to follow them.
 */

import { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  RefreshControl,
  Switch,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useRouter, useFocusEffect } from 'expo-router';
import { classifyError } from '@/api/errors';
import * as Location from 'expo-location';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { useAuthStore } from '@/store/auth-store';
import { useLocationStore } from '@/store/location-store';
import {
  getMyProfile,
  getDonationHistory,
  updateProfile,
  DonationHistory,
} from '@/api/profile';
import { logoutApi } from '@/api/auth';
import { ProfileEditModal } from '@/components/ProfileEditModal';

function describeError(err: unknown, fallback: string): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR' ? fallback : failure.message;
}

export default function ProfileScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const logout = useAuthStore((s) => s.logout);
  const setStoredLocation = useLocationStore((s) => s.setLocation);
  const [donations, setDonations] = useState<DonationHistory[]>([]);
  const [totalDonations, setTotalDonations] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [savingAvailability, setSavingAvailability] = useState(false);
  const [savingLocation, setSavingLocation] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    setError('');
    try {
      const [profile, history] = await Promise.all([
        getMyProfile(),
        getDonationHistory(),
      ]);
      setUser(profile);
      setDonations(history.items);
      setTotalDonations(history.total);
    } catch (e) {
      setError(describeError(e, 'Could not load your profile.'));
    } finally {
      setLoading(false);
    }
  };

  useFocusEffect(
    useCallback(() => {
      fetchData();
    }, [])
  );

  const handleToggleAvailability = async (value: boolean) => {
    if (!user) return;
    setSavingAvailability(true);
    // Optimistic: a switch that lags behind the finger feels broken.
    setUser({ ...user, is_available: value });
    try {
      const updated = await updateProfile({ is_available: value });
      setUser(updated);
    } catch (e) {
      setUser({ ...user, is_available: !value });
      Alert.alert('Error', describeError(e, 'Could not update your availability.'));
    } finally {
      setSavingAvailability(false);
    }
  };

  /**
   * Push the device's current position to the profile.
   *
   * This is what donor search and nearby-request matching read, so it is worth
   * an explicit control: the coordinates captured at sign-up go stale the moment
   * someone travels.
   */
  const handleUpdateLocation = async () => {
    setSavingLocation(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert(
          'Location needed',
          'Enable location access for this app in your device settings to update your position.'
        );
        return;
      }

      const position = await Location.getCurrentPositionAsync({});
      const { latitude, longitude } = position.coords;

      // Argument order is (latitude, longitude) — see the note in location-store.
      setStoredLocation(latitude, longitude);
      const updated = await updateProfile({ latitude, longitude });
      setUser(updated);
      Alert.alert('Location updated', 'Donors and requests will now be matched to where you are.');
    } catch (e) {
      Alert.alert('Error', describeError(e, 'Could not update your location. Check that GPS is enabled.'));
    } finally {
      setSavingLocation(false);
    }
  };

  const handleLogout = () => {
    Alert.alert('Logout', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: async () => {
          try {
            if (refreshToken) await logoutApi(refreshToken);
          } catch (e) {
            // Revoking server-side is best-effort; the local session goes either way.
          }
          // Awaited: logout clears SecureStore, and navigating before that
          // finishes would let the next launch find tokens that should be gone.
          await logout();
          router.replace('/');
        },
      },
    ]);
  };

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.background }}
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={fetchData} tintColor={colors.primary} />}
    >
      {/* Profile Header */}
      <View style={styles.profileHeader}>
        <View style={[styles.avatarCircle, { backgroundColor: colors.primaryLight }]}>
          <Text style={styles.avatarText}>
            {user?.name?.charAt(0)?.toUpperCase() || '?'}
          </Text>
        </View>
        <Text style={[styles.userName, { color: colors.text }]}>{user?.name}</Text>
        <Text style={[styles.userEmail, { color: colors.textSecondary }]}>{user?.email}</Text>
        <View style={[styles.bloodBadgeLarge, { backgroundColor: colors.bloodBadge }]}>
          <Text style={[styles.bloodBadgeText, { color: colors.bloodBadgeText }]}>
            {user?.blood_group || '?'}
          </Text>
        </View>
      </View>

      {error ? (
        <Text style={[styles.errorBanner, { color: colors.error, backgroundColor: colors.errorLight }]}>
          {error}
        </Text>
      ) : null}

      {/* Donor controls — the two settings that decide whether this account can
          be matched at all. */}
      <View style={[styles.section, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <Text style={[styles.sectionTitle, { color: colors.text }]}>Donor Settings</Text>

        <View style={[styles.settingRow, { borderBottomColor: colors.divider, borderBottomWidth: 1 }]}>
          <View style={{ flex: 1, paddingRight: Spacing.md }}>
            <Text style={[styles.settingLabel, { color: colors.text }]}>
              Available to donate
            </Text>
            <Text style={[styles.settingHint, { color: colors.textSecondary }]}>
              When off, you won&apos;t appear in donor searches or get request alerts.
            </Text>
          </View>
          <Switch
            value={!!user?.is_available}
            onValueChange={handleToggleAvailability}
            disabled={savingAvailability || !user}
            trackColor={{ true: colors.primary, false: colors.border }}
          />
        </View>

        <Pressable
          style={styles.settingRow}
          onPress={handleUpdateLocation}
          disabled={savingLocation}
        >
          <View style={{ flex: 1, paddingRight: Spacing.md }}>
            <Text style={[styles.settingLabel, { color: colors.text }]}>
              Update my location
            </Text>
            <Text style={[styles.settingHint, { color: colors.textSecondary }]}>
              {user?.latitude != null && user?.longitude != null
                ? `Saved: ${user.latitude.toFixed(4)}, ${user.longitude.toFixed(4)}`
                : 'Not set — nearby matching needs this'}
            </Text>
          </View>
          {savingLocation ? (
            <ActivityIndicator size="small" color={colors.primary} />
          ) : (
            <Text style={[styles.settingAction, { color: colors.primary }]}>Update</Text>
          )}
        </Pressable>
      </View>

      {/* Profile Details */}
      <View style={[styles.section, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <View style={styles.sectionHeaderRow}>
          <Text style={[styles.sectionTitle, { color: colors.text }]}>Profile Details</Text>
          <Pressable onPress={() => setEditing(true)} hitSlop={8} disabled={!user}>
            <Text style={[styles.settingAction, { color: colors.primary }]}>Edit</Text>
          </Pressable>
        </View>

        <ProfileRow label="Phone" value={user?.phone || 'Not set'} colors={colors} />
        <ProfileRow label="Gender" value={user?.gender || 'Not set'} colors={colors} />
        <ProfileRow label="Date of Birth" value={user?.date_of_birth || 'Not set'} colors={colors} />
        <ProfileRow label="Division" value={user?.division || 'Not set'} colors={colors} />
        <ProfileRow label="District" value={user?.district || 'Not set'} colors={colors} />
        <ProfileRow label="Upazila" value={user?.upazila || 'Not set'} colors={colors} />
        <ProfileRow
          label="Available"
          value={user?.is_available ? 'Yes ✓' : 'No'}
          colors={colors}
          valueColor={user?.is_available ? colors.success : colors.error}
        />
        <ProfileRow label="Last Donation" value={user?.last_donation_date || 'Never'} colors={colors} />
      </View>

      {/* Donation History */}
      <View style={[styles.section, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <Text style={[styles.sectionTitle, { color: colors.text }]}>
          Donation History ({totalDonations})
        </Text>

        {donations.length === 0 ? (
          <Text style={[styles.emptyText, { color: colors.textTertiary }]}>
            No donations yet. Your generosity awaits!
          </Text>
        ) : (
          donations.map((d) => (
            <View key={d.id} style={[styles.donationItem, { borderBottomColor: colors.divider }]}>
              <View>
                <Text style={[styles.donationDate, { color: colors.text }]}>{d.date}</Text>
                <Text style={[styles.donationDetail, { color: colors.textSecondary }]}>
                  {d.blood_group} • {d.hospital || 'Unknown hospital'}
                </Text>
              </View>
              <View style={[styles.donationStatus, { backgroundColor: colors.successLight }]}>
                <Text style={[styles.donationStatusText, { color: colors.success }]}>{d.status}</Text>
              </View>
            </View>
          ))
        )}
      </View>

      {/* Logout */}
      <Pressable
        style={[styles.logoutButton, { borderColor: colors.error }]}
        onPress={handleLogout}
      >
        <Text style={[styles.logoutText, { color: colors.error }]}>Sign Out</Text>
      </Pressable>

      {user && (
        <ProfileEditModal
          visible={editing}
          profile={user}
          onClose={() => setEditing(false)}
          onSaved={setUser}
        />
      )}
    </ScrollView>
  );
}

function ProfileRow({
  label,
  value,
  colors,
  valueColor,
}: {
  label: string;
  value: string;
  colors: any;
  valueColor?: string;
}) {
  return (
    <View style={[profileRowStyles.row, { borderBottomColor: colors.divider }]}>
      <Text style={[profileRowStyles.label, { color: colors.textSecondary }]}>{label}</Text>
      <Text style={[profileRowStyles.value, { color: valueColor || colors.text }]}>{value}</Text>
    </View>
  );
}

const profileRowStyles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: Spacing.md,
    borderBottomWidth: 1,
  },
  label: {
    fontSize: Typography.sizes.sm,
  },
  value: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
  },
});

const styles = StyleSheet.create({
  container: {
    padding: Spacing.xl,
    paddingTop: 60,
    paddingBottom: 100,
  },
  profileHeader: {
    alignItems: 'center',
    marginBottom: Spacing.xl,
  },
  avatarCircle: {
    width: 80,
    height: 80,
    borderRadius: 40,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: Spacing.md,
  },
  avatarText: {
    fontSize: 32,
    fontWeight: '800',
    color: '#DC2626',
  },
  userName: {
    fontSize: Typography.sizes.xl,
    fontWeight: '800',
  },
  userEmail: {
    fontSize: Typography.sizes.sm,
    marginTop: 4,
  },
  bloodBadgeLarge: {
    marginTop: Spacing.md,
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.full,
  },
  bloodBadgeText: {
    fontSize: Typography.sizes.md,
    fontWeight: '800',
  },
  section: {
    borderRadius: Radius.lg,
    padding: Spacing.base,
    borderWidth: 1,
    marginBottom: Spacing.base,
  },
  sectionTitle: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
    marginBottom: Spacing.sm,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  settingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: Spacing.md,
    borderBottomWidth: 0,
  },
  settingLabel: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
  },
  settingHint: {
    fontSize: Typography.sizes.xs,
    marginTop: 2,
    lineHeight: 16,
  },
  settingAction: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  errorBanner: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
    padding: Spacing.md,
    borderRadius: Radius.md,
    marginBottom: Spacing.base,
    textAlign: 'center',
  },
  emptyText: {
    fontSize: Typography.sizes.sm,
    textAlign: 'center',
    paddingVertical: Spacing.lg,
  },
  donationItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: Spacing.md,
    borderBottomWidth: 1,
  },
  donationDate: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
  },
  donationDetail: {
    fontSize: Typography.sizes.xs,
    marginTop: 2,
  },
  donationStatus: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs,
    borderRadius: Radius.full,
  },
  donationStatusText: {
    fontSize: Typography.sizes.xs,
    fontWeight: '700',
  },
  logoutButton: {
    borderWidth: 1,
    borderRadius: Radius.md,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: Spacing.md,
  },
  logoutText: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
  },
});
