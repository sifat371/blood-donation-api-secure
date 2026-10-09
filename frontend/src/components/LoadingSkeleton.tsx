import { StyleSheet, Animated } from 'react-native';
import { useThemeColors, Radius } from '@/theme';
import { useEffect, useState } from 'react';

export function LoadingSkeleton({ width, height, style }: { width?: number | string; height: number; style?: any }) {
  const colors = useThemeColors();
  // `useState` with a lazy initialiser rather than `useRef(...).current`:
  // `reactCompiler` is enabled, and reading a ref during render — which
  // `interpolate()` below does — is flagged as unsafe because a memoized render
  // may not see the ref's current value. This gives the same create-once value
  // without the ref.
  const [animatedValue] = useState(() => new Animated.Value(0));

  useEffect(() => {
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(animatedValue, {
          toValue: 1,
          duration: 1000,
          useNativeDriver: true,
        }),
        Animated.timing(animatedValue, {
          toValue: 0,
          duration: 1000,
          useNativeDriver: true,
        }),
      ])
    );
    animation.start();
    // Skeletons unmount as soon as the data they stand in for arrives, which on
    // a list screen is constantly. An unstopped loop keeps driving frames for a
    // view that is gone.
    return () => animation.stop();
  }, [animatedValue]);

  const opacity = animatedValue.interpolate({
    inputRange: [0, 1],
    outputRange: [0.3, 0.7],
  });

  return (
    <Animated.View
      style={[
        styles.skeleton,
        {
          width: width || '100%',
          height,
          backgroundColor: colors.surfaceVariant,
          opacity,
        },
        style,
      ]}
    />
  );
}

const styles = StyleSheet.create({
  skeleton: {
    borderRadius: Radius.md,
  },
});
