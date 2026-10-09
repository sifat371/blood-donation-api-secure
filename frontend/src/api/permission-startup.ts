import { loadTokens } from "@/store/auth-storage";
import { useAuthStore } from "@/store/auth-store";
import { useLocationStore } from "@/store/location-store";
import { useNotificationStore } from "@/store/notification-store";
import { getApp } from "@react-native-firebase/app";
import { getMessaging, getToken } from "@react-native-firebase/messaging";
import * as Location from "expo-location";
import * as Notifications from "expo-notifications";
import { classifyError } from "./errors";
import { getMyProfile } from "./profile";

export async function getLocationPermission() {
  const location = await Location.requestForegroundPermissionsAsync();

  useLocationStore
    .getState()
    .setPermissionGranted(location.status === "granted" ? "granted" : "denied");

  if (location.status === "granted") {
    // Argument order is (latitude, longitude) — see the note in location-store.
    const current = await Location.getCurrentPositionAsync({});

    useLocationStore
      .getState()
      .setLocation(current.coords.latitude, current.coords.longitude);
  }
}

export async function getNotificationPermission() {
  const notification = await Notifications.requestPermissionsAsync();
  useNotificationStore
    .getState()
    .setPermissionGranted(
      notification.status === "granted" ? "granted" : "denied",
    );

  if (notification.status !== "granted") {
    return;
  }

  // Firebase is a native module: on a build without google-services.json, or in
  // Expo Go, getApp() throws. Push is optional — in-app notifications work
  // regardless — so this must never take the whole startup down with it.
  try {
    const messaging = getMessaging(getApp());
    const token = await getToken(messaging);
    useNotificationStore.getState().setPushToken(token);
  } catch (error) {
    console.warn(
      "Push notifications unavailable (Firebase not configured on this build):",
      error,
    );
  }
}

export async function initializeAuth() {
  const { access, refresh } = await loadTokens();

  if (access && refresh) {
    // hydrateTokens, not setTokens: these values just came *out* of SecureStore,
    // so writing them straight back would be a pointless round-trip.
    useAuthStore.getState().hydrateTokens(access, refresh);
    try {
      const profile = await getMyProfile();
      useAuthStore.getState().setUser(profile);
    } catch (error) {
      // An expired access token is already handled further down the stack: the
      // response interceptor refreshes, retries, and only signs out when the
      // refresh itself is rejected. So a 401 arriving here means the session is
      // genuinely dead.
      //
      // A 403 EMAIL_NOT_VERIFIED means the account exists but is not active.
      // Keeping the session would drop the user into the app with every screen
      // refusing to load; clearing it puts them on the login screen, which sends
      // them to the verification step. In practice this should be unreachable —
      // no endpoint issues tokens to an unverified account — but the cost of
      // being wrong is an app the user cannot get out of.
      //
      // Everything else — backend down, wrong LAN address, DNS failure, timeout
      // — must NOT sign the user out. The tokens are still perfectly valid, and
      // discarding them would force a fresh login every time the API happens to
      // be unreachable, which during device testing is most of the time.
      const failure = classifyError(error);
      if (
        failure.kind === "SESSION_EXPIRED" ||
        failure.kind === "EMAIL_NOT_VERIFIED" ||
        failure.status === 401
      ) {
        await useAuthStore.getState().logout();
      } else {
        console.warn(
          "Could not load profile at startup — keeping the saved session:",
          error,
        );
      }
    }
  }

  useAuthStore.getState().setLoading(false);
}
