import { View, Text, StyleSheet } from 'react-native';
import { useThemeColors, Typography, Radius } from '@/theme';

export function StatusBadge({ status }: { status: string }) {
  const colors = useThemeColors();

  let bgColor = colors.textSecondary;
  let textColor = colors.textSecondary;

  switch (status) {
    case 'Pending':
      bgColor = colors.pending + '20';
      textColor = colors.pending;
      break;
    case 'Accepted':
      bgColor = colors.accepted + '20';
      textColor = colors.accepted;
      break;
    case 'Completed':
      bgColor = colors.completed + '20';
      textColor = colors.completed;
      break;
    case 'Cancelled':
      bgColor = colors.cancelled + '20';
      textColor = colors.cancelled;
      break;
  }

  return (
    <View style={[styles.badge, { backgroundColor: bgColor }]}>
      <Text style={[styles.text, { color: textColor }]}>{status}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: Radius.full,
  },
  text: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
});
