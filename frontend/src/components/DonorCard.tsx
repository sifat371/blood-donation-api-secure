import { View, Text, StyleSheet } from 'react-native';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { Donor } from '@/api/donors';

export function DonorCard({ donor }: { donor: Donor }) {
  const colors = useThemeColors();

  return (
    <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <View style={styles.header}>
        <View style={[styles.bloodBadge, { backgroundColor: colors.bloodBadge }]}>
          <Text style={[styles.bloodText, { color: colors.bloodBadgeText }]}>{donor.blood_group}</Text>
        </View>
        <View style={styles.infoContainer}>
          <Text style={[styles.name, { color: colors.text }]}>{donor.name}</Text>
          <Text style={[styles.distance, { color: colors.textSecondary }]}>
            {donor.distance_km} km away • {donor.district || 'Unknown area'}
          </Text>
        </View>
      </View>
      {donor.last_donation_date && (
        <Text style={[styles.lastDonation, { color: colors.textTertiary }]}>
          Last donated: {donor.last_donation_date}
        </Text>
      )}
      <Text style={[styles.lastDonation, { color: colors.textSecondary }]}>
        Phone number private — notify via a blood request.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: Radius.lg,
    padding: Spacing.base,
    borderWidth: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  bloodBadge: {
    width: 44,
    height: 44,
    borderRadius: 22,
    justifyContent: 'center',
    alignItems: 'center',
  },
  bloodText: {
    fontSize: Typography.sizes.sm,
    fontWeight: '800',
  },
  infoContainer: {
    flex: 1,
    marginLeft: Spacing.md,
  },
  name: {
    fontSize: Typography.sizes.base,
    fontWeight: '700',
  },
  distance: {
    fontSize: Typography.sizes.sm,
    marginTop: 2,
  },
  lastDonation: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.sm,
    marginLeft: 56,
  },
});
