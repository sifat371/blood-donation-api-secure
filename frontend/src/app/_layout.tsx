/**
 * Root layout — wraps the entire app with providers.
 *
 * - React Query QueryClientProvider
 * - React Native Paper PaperProvider
 * - Theme-aware navigation
 * - Auth-gated routing
 * - Notification tap → deep link into the referenced blood request
 */

import { openNotificationTarget } from "@/api/notifications";
import {
  getLocationPermission,
  getNotificationPermission,
  initializeAuth,
} from "@/api/permission-startup";
import { Colors } from "@/theme";
import { getApp } from "@react-native-firebase/app";
import {
  getInitialNotification,
  getMessaging,
  onMessage,
  onNotificationOpenedApp,
} from "@react-native-firebase/messaging";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Notifications from "expo-notifications";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect, useState } from "react";
import { Platform, StatusBar, useColorScheme } from "react-native";
import { MD3DarkTheme, MD3LightTheme, PaperProvider } from "react-native-paper";

// Fire-and-forget: on some platforms this rejects when the splash screen is
// already gone, and an unhandled rejection at module scope is not worth it.
SplashScreen.preventAutoHideAsync().catch(() => {});

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 30_000,
    },
  },
});

const lightTheme = {
  ...MD3LightTheme,
  colors: {
    ...MD3LightTheme.colors,
    primary: Colors.light.primary,
    secondary: Colors.light.secondary,
    background: Colors.light.background,
    surface: Colors.light.surface,
    error: Colors.light.error,
  },
};

const darkTheme = {
  ...MD3DarkTheme,
  colors: {
    ...MD3DarkTheme.colors,
    primary: Colors.dark.primary,
    secondary: Colors.dark.secondary,
    background: Colors.dark.background,
    surface: Colors.dark.surface,
    error: Colors.dark.error,
  },
};

export default function RootLayout() {
  const colorScheme = useColorScheme();
  const theme = colorScheme === "dark" ? darkTheme : lightTheme;
  const [appReady, setAppReady] = useState(false);

  useEffect(() => {
    async function bootstrap() {
      // Each step is isolated and non-fatal. The render below is gated on
      // appReady, so anything that escapes here leaves the user staring at a
      // blank screen forever — which is exactly what used to happen when the
      // backend was unreachable or the location prompt threw.
      try {
        await initializeAuth();
      } catch (error) {
        console.warn("Auth restore failed at startup:", error);
      }

      // Permissions are requested in parallel but neither can sink the other:
      // allSettled instead of all. Both are optional — the app is usable with
      // location and push denied.
      await Promise.allSettled([
        getLocationPermission(),
        getNotificationPermission(),
      ]);

      setAppReady(true);
      await SplashScreen.hideAsync().catch(() => {});
    }

    bootstrap().catch(async (error) => {
      // Belt and braces: reveal the UI no matter what.
      console.warn("Startup did not complete cleanly:", error);
      setAppReady(true);
      await SplashScreen.hideAsync().catch(() => {});
    });
  }, []);

  // Notification taps. Two sources, one destination:
  //   - expo-notifications: local notifications, including the ones this app
  //     schedules from foreground FCM messages below.
  //   - Firebase messaging: notifications the OS displayed while the app was in
  //     the background or not running.
  useEffect(() => {
    const receivedSubscription = Notifications.addNotificationReceivedListener(
      () => {
        // Displayed by the OS; nothing to do until the user taps it.
      },
    );

    const responseSubscription =
      Notifications.addNotificationResponseReceivedListener((response) => {
        openNotificationTarget(
          response.notification.request.content.data,
        );
      });

    // Cold start: the tap that launched the app has no listener to fire into.
    Notifications.getLastNotificationResponseAsync()
      .then((response) => {
        if (response) {
          openNotificationTarget(response.notification.request.content.data);
        }
      })
      .catch(() => {});

    return () => {
      receivedSubscription.remove();
      responseSubscription.remove();
    };
  }, []);

  useEffect(() => {
    // Android channel setup (একবারই দরকার)
    if (Platform.OS === "android") {
      Notifications.setNotificationChannelAsync("default", {
        name: "default",
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 250, 250, 250],
      });
    }

    // Firebase is a native module. On a build without google-services.json — or
    // in Expo Go — getApp() throws, and an exception here used to take the whole
    // root layout down. Push is a nice-to-have; in-app notifications are not.
    let messaging: ReturnType<typeof getMessaging>;
    try {
      messaging = getMessaging(getApp());
    } catch (error) {
      console.warn(
        "Firebase messaging unavailable on this build; push notifications are off:",
        error,
      );
      return;
    }

    // Foreground-এ FCM message আসলে
    const unsubscribeForeground = onMessage(messaging, async (remoteMessage) => {
      await Notifications.scheduleNotificationAsync({
        content: {
          title: remoteMessage.notification?.title ?? "New Notification",
          body: remoteMessage.notification?.body ?? "",
          data: remoteMessage.data ?? {},
        },
        trigger: null,
      });
    });

    // Tapped while the app was backgrounded.
    const unsubscribeOpened = onNotificationOpenedApp(
      messaging,
      (remoteMessage) => {
        openNotificationTarget(remoteMessage?.data);
      },
    );

    // Tapped while the app was not running at all.
    getInitialNotification(messaging)
      .then((remoteMessage) => {
        if (remoteMessage) openNotificationTarget(remoteMessage.data);
      })
      .catch(() => {});

    return () => {
      unsubscribeForeground();
      unsubscribeOpened();
    };
  }, []);

  if (!appReady) return null;

  return (
    <QueryClientProvider client={queryClient}>
      <PaperProvider theme={theme}>
        <StatusBar
          barStyle={colorScheme === "dark" ? "light-content" : "dark-content"}
          backgroundColor={
            colorScheme === "dark"
              ? Colors.dark.background
              : Colors.light.background
          }
        />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="index" />
          <Stack.Screen name="(auth)" options={{ animation: "fade" }} />
          <Stack.Screen name="(main)" options={{ animation: "fade" }} />
        </Stack>
      </PaperProvider>
    </QueryClientProvider>
  );
}
