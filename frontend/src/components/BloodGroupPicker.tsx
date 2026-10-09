import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';

const BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];

interface Props {
  selected: string;
  onSelect: (bg: string) => void;
}

export function BloodGroupPicker({ selected, onSelect }: Props) {
  const colors = useThemeColors();

  return (
    <View style={styles.container}>
      {BLOOD_GROUPS.map((bg) => (
        <Pressable
          key={bg}
          style={[
            styles.chip,
            {
              backgroundColor: selected === bg ? colors.primary : colors.surfaceVariant,
              borderColor: selected === bg ? colors.primary : colors.border,
            },
          ]}
          onPress={() => onSelect(bg)}
        >
          <Text
            style={[
              styles.chipText,
              { color: selected === bg ? colors.textOnPrimary : colors.text },
            ]}
          >
            {bg}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.sm,
    marginVertical: Spacing.sm,
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
});
