import { View, Text, StyleSheet } from 'react-native';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { BloodRequest } from '@/api/blood-requests';
import { StatusBadge } from './StatusBadge';

interface Props {
  request: BloodRequest;
}

export function RequestCard({ request }: Props) {
  const colors = useThemeColors();

  return (
    <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <View style={styles.header}>
        <View style={[styles.bloodBadge, { backgroundColor: colors.bloodBadge }]}>
          <Text style={[styles.bloodText, { color: colors.bloodBadgeText }]}>{request.blood_group}</Text>
        </View>
        <StatusBadge status={request.status} />
      </View>
      <Text style={[styles.name, { color: colors.text }]}>{request.patient_name || 'Patient details shared after commitment'}</Text>
      <Text style={[styles.hospital, { color: colors.textSecondary }]}>🏥 {request.hospital_name}</Text>
      <Text style={[styles.detail, { color: colors.textTertiary }]}>
        {request.units} unit(s) • Needed: {request.needed_date}
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
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.sm,
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
  name: {
    fontSize: Typography.sizes.md,
    fontWeight: '700',
  },
  hospital: {
    fontSize: Typography.sizes.sm,
    marginTop: 4,
  },
  detail: {
    fontSize: Typography.sizes.xs,
    marginTop: 4,
  },
});
