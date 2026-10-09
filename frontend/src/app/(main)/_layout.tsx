/**
 * Main tab navigator layout — bottom tabs for Home, Find Donor,
 * Requests, Notifications, Profile.
 *
 * Also the auth gate for everything under `(main)`. The root index screen picks
 * a destination once at launch; this guard keeps holding afterwards, which is
 * what handles a session ending mid-use — a refresh token that gets rejected
 * makes the API client log out, and without this the user would sit on an
 * authenticated screen watching every request fail. It also catches a push
 * notification deep-link that arrives while signed out.
 */

import { registerFcmToken } from "@/api/profile";
import { useAuthStore } from "@/store/auth-store";
import { useNotificationStore } from "@/store/notification-store";
import { Typography, useThemeColors } from "@/theme";
import { Redirect, Tabs } from "expo-router";
import { useEffect } from "react";
import { ColorValue, StyleSheet, Text, View } from "react-native";

function TabIcon({
  label,
  emoji,
  focused,
  color,
}: {
  label: string;
  emoji: string;
  focused: boolean;
  color: ColorValue;
}) {
  return (
    <View style={styles.tabIconContainer}>
      <Text style={[styles.tabEmoji, focused && styles.tabEmojiActive]}>
        {emoji}
      </Text>
    </View>
  );
}

export default function MainLayout() {
  const colors = useThemeColors();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isProfileComplete = useAuthStore((s) => s.isProfileComplete);
  const user = useAuthStore((s) => s.user);
  const pushToken = useNotificationStore((s) => s.pushToken);

  useEffect(() => {
    if (!isAuthenticated || !pushToken) {
      return;
    }

    /**
     * Bind this device's push token to the signed-in account.
     *
     * Runs on every auth change, not just once, which is what makes two accounts
     * on one device behave: signing in as someone else re-registers the token
     * under the new user, so alerts follow the session rather than the handset.
     *
     * Failure is non-fatal — push is an extra channel, and the in-app
     * notifications list still works — but it is logged rather than swallowed,
     * because "no alerts arrive" is otherwise impossible to diagnose.
     */
    const syncToken = async () => {
      try {
        await registerFcmToken(pushToken);
      } catch (error) {
        console.warn(
          "Could not register this device for push notifications; in-app alerts still work:",
          error,
        );
      }
    };

    syncToken();
  }, [isAuthenticated, pushToken]);

  // Declared after the hooks so the effect ordering above stays stable across
  // renders whichever way the guard falls.
  if (!isAuthenticated) {
    return <Redirect href="/(auth)/login" />;
  }
  // Note the `user &&`: isProfileComplete is false both for a genuinely
  // unfinished profile and for one that hasn't loaded yet, and at startup the
  // second happens whenever the backend is unreachable. Without the guard, a
  // dropped connection would drop a long-registered user into the signup form.
  if (user && !isProfileComplete) {
    return <Redirect href="/(auth)/complete-profile" />;
  }

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.tabBarActive,
        tabBarInactiveTintColor: colors.tabBarInactive,
        tabBarStyle: {
          backgroundColor: colors.tabBar,
          borderTopColor: colors.border,
          borderTopWidth: 1,
          height: 65,
          paddingBottom: 8,
          paddingTop: 8,
        },
        tabBarLabelStyle: {
          fontSize: Typography.sizes.xs,
          fontWeight: "600",
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Home",
          tabBarIcon: ({ focused, color }) => (
            <TabIcon label="Home" emoji="🏠" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="find-donor"
        options={{
          title: "Find Donor",
          tabBarIcon: ({ focused, color }) => (
            <TabIcon label="Find" emoji="🔍" focused={focused} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="requests"
        options={{
          title: "Requests",
          tabBarIcon: ({ focused, color }) => (
            <TabIcon
              label="Requests"
              emoji="📋"
              focused={focused}
              color={color}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="notifications"
        options={{
          title: "Alerts",
          tabBarIcon: ({ focused, color }) => (
            <TabIcon
              label="Alerts"
              emoji="🔔"
              focused={focused}
              color={color}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: "Profile",
          tabBarIcon: ({ focused, color }) => (
            <TabIcon
              label="Profile"
              emoji="👤"
              focused={focused}
              color={color}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="chat"
        options={{
          title: "AI Chat",
          tabBarIcon: ({ focused, color }) => (
            <TabIcon label="Chat" emoji="💬" focused={focused} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabIconContainer: {
    alignItems: "center",
    justifyContent: "center",
  },
  tabEmoji: {
    fontSize: 22,
    opacity: 0.6,
  },
  tabEmojiActive: {
    opacity: 1,
    transform: [{ scale: 1.15 }],
  },
});
