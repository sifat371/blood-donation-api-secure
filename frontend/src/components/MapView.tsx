/**
 * Location card — shows a real position and hands off to the device's map app.
 *
 * Deliberately not an embedded map. Adding `react-native-maps` means a native
 * dependency, an API key per platform, and a fresh prebuild of the existing
 * `android/` directory — a lot of risk for a screen whose actual job is "tell me
 * where this is and get me there." The system map app already does turn-by-turn
 * navigation better than an embedded view would, so this renders the coordinates
 * plus any markers and opens the real thing on tap.
 *
 * Needs no location permission: it displays coordinates it is handed and asks
 * the OS to open a URL, so there is nothing here to deny and nothing to crash.
 */

import { Linking, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { Radius, Spacing, Typography, useThemeColors } from '@/theme';

export interface MapMarker {
  /** What sits at this point — a hospital, a donor, the user. */
  label: string;
  latitude: number;
  longitude: number;
  /** Optional second line, e.g. an address or "3.2 km away". */
  detail?: string;
}

/**
 * Open a coordinate in whatever map app the device has.
 *
 * The platform schemes are tried first because they land in the native app with
 * the pin already dropped; the Google Maps web URL is the fallback for anything
 * that can't handle them (including web builds and stripped-down Android ROMs).
 */
export async function openInMaps(latitude: number, longitude: number, label?: string) {
  const coords = `${latitude},${longitude}`;
  const encodedLabel = encodeURIComponent(label ?? coords);
  const nativeUrl =
    Platform.OS === 'ios'
      ? `maps://?q=${encodedLabel}&ll=${coords}`
      : `geo:${coords}?q=${coords}(${encodedLabel})`;
  const webUrl = `https://www.google.com/maps/search/?api=1&query=${coords}`;

  try {
    await Linking.openURL(nativeUrl);
  } catch {
    // No map app, or the scheme isn't registered. The browser always is.
    try {
      await Linking.openURL(webUrl);
    } catch {
      // Nothing can open it; the coordinates stay on screen either way.
    }
  }
}

export function MapView({
  markers,
  emptyMessage = 'No location attached.',
}: {
  markers: MapMarker[];
  emptyMessage?: string;
}) {
  const colors = useThemeColors();

  if (markers.length === 0) {
    return (
      <View
        style={[
          styles.container,
          { backgroundColor: colors.surfaceVariant, borderColor: colors.border },
        ]}
      >
        <Text style={[styles.emptyText, { color: colors.textSecondary }]}>{emptyMessage}</Text>
      </View>
    );
  }

  return (
    <View
      style={[
        styles.container,
        { backgroundColor: colors.surfaceVariant, borderColor: colors.border },
      ]}
    >
      {markers.map((marker) => (
        <Pressable
          key={`${marker.label}-${marker.latitude}-${marker.longitude}`}
          style={({ pressed }) => [styles.marker, { opacity: pressed ? 0.6 : 1 }]}
          onPress={() => openInMaps(marker.latitude, marker.longitude, marker.label)}
        >
          <Text style={styles.pin}>📍</Text>
          <View style={{ flex: 1 }}>
            <Text style={[styles.label, { color: colors.text }]}>{marker.label}</Text>
            {marker.detail ? (
              <Text style={[styles.detail, { color: colors.textSecondary }]}>{marker.detail}</Text>
            ) : null}
            <Text style={[styles.coords, { color: colors.textTertiary }]}>
              {marker.latitude.toFixed(5)}, {marker.longitude.toFixed(5)}
            </Text>
          </View>
          <Text style={[styles.action, { color: colors.primary }]}>Directions</Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    borderRadius: Radius.lg,
    borderWidth: 1,
    padding: Spacing.sm,
    gap: Spacing.xs,
  },
  marker: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.md,
    padding: Spacing.md,
  },
  pin: {
    fontSize: 22,
  },
  label: {
    fontSize: Typography.sizes.sm,
    fontWeight: '700',
  },
  detail: {
    fontSize: Typography.sizes.xs,
    marginTop: 2,
  },
  coords: {
    fontSize: Typography.sizes.xs,
    marginTop: 2,
    fontVariant: ['tabular-nums'],
  },
  action: {
    fontSize: Typography.sizes.xs,
    fontWeight: '700',
  },
  emptyText: {
    fontSize: Typography.sizes.sm,
    textAlign: 'center',
    paddingVertical: Spacing.lg,
  },
});
