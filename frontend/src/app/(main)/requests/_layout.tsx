/**
 * Requests stack layout for nested navigation.
 */

import { Stack } from 'expo-router';
import { useThemeColors } from '@/theme';

export default function RequestsLayout() {
  const colors = useThemeColors();
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
      }}
    >
      <Stack.Screen name="index" />
      <Stack.Screen
        name="create"
        options={{
          headerShown: true,
          title: 'Create Request',
          presentation: 'modal',
        }}
      />
      {/* The request detail screen. Declaring it here is what makes the donor
          accept flow reachable — the screen file existed but was never routed,
          so nothing could navigate to it. */}
      <Stack.Screen name="[id]" />
    </Stack>
  );
}
