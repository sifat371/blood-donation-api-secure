/**
 * Find Donor screen — search donors by blood group and location.
 */

import { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  ActivityIndicator,
} from 'react-native';
import { classifyError } from '@/api/errors';
import { useRouter } from 'expo-router';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { useAuthStore } from '@/store/auth-store';
import { useLocationStore } from '@/store/location-store';
import { getLocationPermission } from '@/api/permission-startup';
import { searchDonors, Donor } from '@/api/donors';

const BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];

const SEARCH_RADIUS_KM = 50;

function describeError(err: unknown): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR'
    ? 'Donor search failed. Please try again.'
    : failure.message;
}

export default function FindDonorScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const gpsLatitude = useLocationStore((s) => s.latitude);
  const gpsLongitude = useLocationStore((s) => s.longitude);
  const [selectedBloodGroup, setSelectedBloodGroup] = useState('');
  const [donors, setDonors] = useState<Donor[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');

  /**
   * Live GPS first, the coordinates saved on the profile second, and nothing
   * third.
   *
   * This used to default to the centre of Dhaka when both were missing, which
   * quietly returned donors near a city the user might be nowhere near — and,
   * worse, made the search look like it worked. A search with no location is a
   * search that cannot be answered, so say so.
   */
  const resolveCoordinates = () => {
    const latitude = gpsLatitude ?? user?.latitude ?? null;
    const longitude = gpsLongitude ?? user?.longitude ?? null;
    if (latitude == null || longitude == null) return null;
    return { latitude, longitude };
  };

  const handleSearch = async () => {
    if (!selectedBloodGroup) return;

    const coords = resolveCoordinates();
    if (!coords) {
      setError(
        'We need your location to find donors near you. Tap "Use My Location" below, or add your location in your profile.'
      );
      setSearched(false);
      setDonors([]);
      setTotal(0);
      return;
    }

    setLoading(true);
    setSearched(true);
    setError('');
    try {
      const result = await searchDonors({
        blood_group: selectedBloodGroup,
        ...coords,
        radius_km: SEARCH_RADIUS_KM,
      });
      setDonors(result.items);
      setTotal(result.total);
    } catch (e) {
      setDonors([]);
      setTotal(0);
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  };

  const useMyLocation = async () => {
    setLoading(true);
    setError('');
    try {
      await getLocationPermission();
      // Read the store directly: the hook values in scope are from this render
      // and won't reflect the fix that just landed.
      if (useLocationStore.getState().latitude == null) {
        setError(
          'Location permission was denied. Enable it in your device settings, or add your location in your profile.'
        );
      }
    } catch (e) {
      setError('Could not read your location. Check that GPS is enabled.');
    } finally {
      setLoading(false);
    }
  };

  const renderDonor = ({ item }: { item: Donor }) => (
    <View style={[styles.donorCard, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <View style={styles.donorHeader}>
        <View style={[styles.donorBloodBadge, { backgroundColor: colors.bloodBadge }]}>
          <Text style={[styles.donorBloodText, { color: colors.bloodBadgeText }]}>{item.blood_group}</Text>
        </View>
        <View style={{ flex: 1, marginLeft: Spacing.md }}>
          <Text style={[styles.donorName, { color: colors.text }]}>{item.name}</Text>
          <Text style={[styles.donorInfo, { color: colors.textSecondary }]}>
            {item.distance_km} km away • {item.district || 'Unknown area'}
          </Text>
        </View>
      </View>
      {item.last_donation_date && (
        <Text style={[styles.donorLastDonation, { color: colors.textTertiary }]}>
          Last donated: {item.last_donation_date}
        </Text>
      )}
      <Text style={[styles.donorLastDonation, { color: colors.textSecondary }]}>
        Phone numbers are private. Create a request to notify matching donors.
      </Text>
    </View>
  );

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      {/* Header */}
      <View style={[styles.headerSection, { backgroundColor: colors.surface, borderBottomColor: colors.border }]}>
        <Text style={[styles.title, { color: colors.text }]}>Find Blood Donor</Text>
        <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
          Select blood group to search nearby donors
        </Text>

        {/* Blood group selector */}
        <View style={styles.chipRow}>
          {BLOOD_GROUPS.map((bg) => (
            <Pressable
              key={bg}
              style={[
                styles.chip,
                {
                  backgroundColor: selectedBloodGroup === bg ? colors.primary : colors.surfaceVariant,
                  borderColor: selectedBloodGroup === bg ? colors.primary : colors.border,
                },
              ]}
              onPress={() => setSelectedBloodGroup(bg)}
            >
              <Text
                style={[
                  styles.chipText,
                  { color: selectedBloodGroup === bg ? colors.textOnPrimary : colors.text },
                ]}
              >
                {bg}
              </Text>
            </Pressable>
          ))}
        </View>

        <Pressable
          style={[
            styles.searchButton,
            {
              backgroundColor: selectedBloodGroup ? colors.primary : colors.surfaceVariant,
              opacity: selectedBloodGroup ? 1 : 0.5,
            },
          ]}
          onPress={handleSearch}
          disabled={!selectedBloodGroup || loading}
        >
          {loading ? (
            <ActivityIndicator color={colors.textOnPrimary} />
          ) : (
            <Text style={[styles.searchButtonText, { color: selectedBloodGroup ? colors.textOnPrimary : colors.textTertiary }]}>
              Search Donors
            </Text>
          )}
        </Pressable>

        {/* Where the search is centred. Stated plainly, because a donor list is
            meaningless without knowing what "nearby" meant. */}
        {resolveCoordinates() ? (
          <Text style={[styles.locationNote, { color: colors.textTertiary }]}>
            📍 Searching within {SEARCH_RADIUS_KM} km of your location
          </Text>
        ) : (
          <Pressable
            style={[styles.locationButton, { borderColor: colors.primary }]}
            onPress={useMyLocation}
            disabled={loading}
          >
            <Text style={[styles.locationButtonText, { color: colors.primary }]}>
              📍 Use My Location
            </Text>
          </Pressable>
        )}

        {error ? (
          <Text style={[styles.errorText, { color: colors.error }]}>{error}</Text>
        ) : null}
      </View>

      {/* Phone numbers are private; secure requests notify eligible donors. */}
      {searched && !error && (
        <View style={styles.resultHeader}>
          <Text style={[styles.resultCount, { color: colors.textSecondary }]}>
            {total} donor{total !== 1 ? 's' : ''} found
          </Text>
          {total > 0 && (
            <Pressable
              accessibilityRole="button"
              style={{ paddingVertical: Spacing.sm, paddingHorizontal: Spacing.md,
                borderRadius: Radius.md, backgroundColor: colors.primary,
                marginTop: Spacing.sm }}
              onPress={() => router.push('/(main)/requests/create')}
            >
              <Text style={{ color: colors.textOnPrimary, fontWeight: '700' }}>
                Create a blood request to notify eligible donors
              </Text>
            </Pressable>
          )}
        </View>
      )}

      <FlatList
        data={donors}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderDonor}
        contentContainerStyle={styles.listContent}
        ListEmptyComponent={
          searched && !loading && !error ? (
            <View style={styles.emptyState}>
              <Text style={styles.emptyEmoji}>😔</Text>
              <Text style={[styles.emptyTitle, { color: colors.text }]}>No donors found</Text>
              <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
                Nobody with that blood group is available within {SEARCH_RADIUS_KM} km
                right now. Try a compatible blood group.
              </Text>
            </View>
          ) : null
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
    marginBottom: Spacing.xs,
  },
  subtitle: {
    fontSize: Typography.sizes.sm,
    marginBottom: Spacing.base,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.sm,
    marginBottom: Spacing.base,
  },
  chip: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.full,
    borderWidth: 1,
  },
  chipText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  searchButton: {
    paddingVertical: 14,
    borderRadius: Radius.md,
    alignItems: 'center',
  },
  searchButtonText: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
  },
  locationNote: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.sm,
    textAlign: 'center',
  },
  locationButton: {
    marginTop: Spacing.md,
    paddingVertical: 12,
    borderRadius: Radius.md,
    borderWidth: 1,
    alignItems: 'center',
  },
  locationButtonText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  errorText: {
    fontSize: Typography.sizes.sm,
    marginTop: Spacing.md,
    textAlign: 'center',
    lineHeight: 20,
  },
  resultHeader: {
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.md,
  },
  resultCount: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
  },
  listContent: {
    padding: Spacing.xl,
    paddingTop: 0,
    gap: Spacing.md,
    paddingBottom: 100,
  },
  donorCard: {
    borderRadius: Radius.lg,
    padding: Spacing.base,
    borderWidth: 1,
  },
  donorHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  donorBloodBadge: {
    width: 44,
    height: 44,
    borderRadius: 22,
    justifyContent: 'center',
    alignItems: 'center',
  },
  donorBloodText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '800',
  },
  donorName: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
  },
  donorInfo: {
    fontSize: Typography.sizes.sm,
    marginTop: 2,
  },
  donorLastDonation: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.sm,
    marginLeft: 56,
  },
  callButton: {
    marginTop: Spacing.md,
    paddingVertical: 10,
    borderRadius: Radius.md,
    alignItems: 'center',
  },
  callButtonText: {
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
});
