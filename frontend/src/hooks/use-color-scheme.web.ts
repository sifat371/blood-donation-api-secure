import { useEffect, useState } from 'react';
import { useColorScheme as useRNColorScheme } from 'react-native';

/**
 * To support static rendering, this value needs to be re-calculated on the client side for web
 */
export function useColorScheme() {
  const [hasHydrated, setHasHydrated] = useState(false);

  useEffect(() => {
    // Deliberate: an effect that runs only on the client is how hydration is
    // *detected*; there is no external system to subscribe to. Returning 'light'
    // until this fires is what keeps the server-rendered markup and the first
    // client render identical. This is the stock Expo pattern for web static
    // rendering, so the rule's advice does not apply here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHasHydrated(true);
  }, []);

  const colorScheme = useRNColorScheme();

  if (hasHydrated) {
    return colorScheme;
  }

  return 'light';
}
