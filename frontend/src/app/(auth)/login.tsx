/**
 * Login screen — Google Sign-In and email/password sign-in.
 *
 * Both paths end the same way: tokens into the store, profile fetched, then
 * either the complete-profile screen or the main app. What they do not share is
 * failure handling — a rejected password, an unverified address and an
 * unreachable server are reported as three different things, because they need
 * three different responses from the user.
 */

import { emailLogin, googleLogin } from "@/api/auth";
import { classifyError } from "@/api/errors";
import { getMyProfile } from "@/api/profile";
import { useAuthStore } from "@/store/auth-store";
import { Radius, Spacing, Typography, useThemeColors } from "@/theme";
import {
  GoogleSignin,
  statusCodes,
} from "@react-native-google-signin/google-signin";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Snackbar } from "react-native-paper";

GoogleSignin.configure({
  webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
});

export default function LoginScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const params = useLocalSearchParams<{ email?: string; verified?: string }>();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setUser = useAuthStore((s) => s.setUser);
  const setGoogleID = useAuthStore((s) => s.setGoogleID);
  const setProfileComplete = useAuthStore((s) => s.setProfileComplete);

  const [loading, setLoading] = useState(false);
  const [snackbarVisible, setSnackbarVisible] = useState(false);
  const [snackbarMessage, setSnackbarMessage] = useState("");
  const [email, setEmail] = useState(params.email ?? "");
  const [password, setPassword] = useState("");
  // Opened straight away when arriving from signup or verification — that user
  // is here to type a password, not to look at a Google button.
  const [showEmailForm, setShowEmailForm] = useState(Boolean(params.email));
  const [error, setError] = useState("");
  const [verifiedNoticeDismissed, setVerifiedNoticeDismissed] = useState(false);

  // Two things drive the snackbar: a failed sign-in attempt, and the "email
  // verified" confirmation carried in the route params. The second is derived
  // from the params rather than copied into state by an effect, which would cost
  // an extra render pass to say something already known at first render.
  const verifiedNotice =
    params.verified === "1" && !verifiedNoticeDismissed
      ? "Email verified. You can sign in now."
      : "";
  const snackbarText = snackbarMessage || verifiedNotice;
  const snackbarShown = snackbarVisible || Boolean(verifiedNotice);

  const dismissSnackbar = () => {
    setSnackbarVisible(false);
    setVerifiedNoticeDismissed(true);
  };

  /**
   * Land the session: store tokens, load the profile, choose the next screen.
   */
  const enterApp = async (result: {
    access_token: string;
    refresh_token: string;
    profile_incomplete: boolean;
  }) => {
    await setTokens(result.access_token, result.refresh_token);
    const profile = await getMyProfile();
    setUser(profile);

    if (result.profile_incomplete) {
      router.replace("/(auth)/complete-profile");
    } else {
      setProfileComplete(true);
      router.replace("/(main)");
    }
  };

  const handleEmailLogin = async () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setError("Enter your email address.");
      return;
    }
    if (!trimmedEmail.includes("@")) {
      setError("That doesn't look like an email address.");
      return;
    }
    if (!password) {
      setError("Enter your password.");
      return;
    }

    try {
      setLoading(true);
      setError("");
      setSnackbarVisible(false);

      await enterApp(await emailLogin(trimmedEmail, password));
    } catch (err) {
      const failure = classifyError(err);

      // An unverified account is not a login failure to argue with — it is a
      // step the user has not finished. Take them to it.
      if (failure.kind === "EMAIL_NOT_VERIFIED") {
        router.push({
          pathname: "/(auth)/verify-email",
          params: { email: trimmedEmail, reason: "login" },
        });
        return;
      }

      setError(failure.message);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Get the ID token out of a Google sign-in result.
   *
   * `getTokens()` is only a valid fallback once someone is actually signed in;
   * calling it after a cancelled or empty `signIn()` throws "getTokens requires
   * a user to be signed in", which then surfaces as a mystery error instead of
   * the real one. So it is only attempted when Google reported a user.
   */
  const resolveGoogleIdToken = async (userInfo: any): Promise<string | null> => {
    const directIdToken = userInfo?.data?.idToken ?? userInfo?.idToken;
    if (directIdToken) return directIdToken;

    const signedInUser = userInfo?.data?.user ?? userInfo?.user;
    if (!signedInUser) return null;

    try {
      const tokenResponse = await GoogleSignin.getTokens();
      return tokenResponse.idToken || null;
    } catch {
      return null;
    }
  };

  const handleGoogleSignIn = async () => {
    try {
      setLoading(true);
      setError("");
      setSnackbarVisible(false);
      await GoogleSignin.hasPlayServices();

      const userInfo = await GoogleSignin.signIn();
      const googleUserId = userInfo?.data?.user?.id;
      const idToken = await resolveGoogleIdToken(userInfo);

      if (!idToken) {
        setSnackbarMessage(
          "Google sign-in did not return an ID token. Please try again.",
        );
        setSnackbarVisible(true);
        return;
      }

      const result = await googleLogin(idToken);
      if (googleUserId) setGoogleID(googleUserId);
      await enterApp(result);
    } catch (err: any) {
      if (err?.code === statusCodes.SIGN_IN_CANCELLED) {
        setSnackbarMessage("Google Sign-In was cancelled");
      } else if (err?.code === statusCodes.IN_PROGRESS) {
        setSnackbarMessage("Google Sign-In is already in progress");
      } else if (err?.code === statusCodes.PLAY_SERVICES_NOT_AVAILABLE) {
        setSnackbarMessage("Google Play Services not available or outdated");
      } else {
        setSnackbarMessage(classifyError(err).message);
      }
      setSnackbarVisible(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        contentContainerStyle={[
          styles.container,
          { backgroundColor: colors.background },
        ]}
        keyboardShouldPersistTaps="handled"
      >
        <Snackbar
          visible={snackbarShown}
          onDismiss={dismissSnackbar}
          duration={4000}
          action={{
            label: "Close",
            onPress: dismissSnackbar,
          }}
        >
          {snackbarText}
        </Snackbar>

        {/* Hero Section */}
        <View style={styles.heroSection}>
          <View
            style={[
              styles.iconContainer,
              { backgroundColor: colors.primaryLight },
            ]}
          >
            <Text style={styles.bloodIcon}>🩸</Text>
          </View>

          <Text style={[styles.appTitle, { color: colors.text }]}>
            Blood Donation
          </Text>
          <Text style={[styles.appSubtitle, { color: colors.textSecondary }]}>
            Save lives. Donate blood.
          </Text>
        </View>

        {/* Login Section */}
        <View style={styles.loginSection}>
          <Pressable
            style={[
              styles.googleButton,
              { backgroundColor: colors.surface, borderColor: colors.border },
            ]}
            onPress={handleGoogleSignIn}
            disabled={loading}
          >
            <Text style={styles.googleIcon}>G</Text>
            <Text style={[styles.googleButtonText, { color: colors.text }]}>
              Continue with Google
            </Text>
          </Pressable>

          <View style={styles.dividerRow}>
            <View
              style={[styles.dividerLine, { backgroundColor: colors.divider }]}
            />
            <Text style={[styles.dividerText, { color: colors.textTertiary }]}>
              or
            </Text>
            <View
              style={[styles.dividerLine, { backgroundColor: colors.divider }]}
            />
          </View>

          {showEmailForm ? (
            <View style={styles.emailSection}>
              <Text
                style={[styles.emailLabel, { color: colors.textSecondary }]}
              >
                Sign in with email
              </Text>
              <TextInput
                style={[
                  styles.input,
                  {
                    backgroundColor: colors.surfaceVariant,
                    color: colors.text,
                    borderColor: colors.border,
                  },
                ]}
                placeholder="Email address"
                placeholderTextColor={colors.textTertiary}
                value={email}
                onChangeText={setEmail}
                keyboardType="email-address"
                autoCapitalize="none"
                autoComplete="email"
                textContentType="emailAddress"
              />
              <TextInput
                style={[
                  styles.input,
                  {
                    backgroundColor: colors.surfaceVariant,
                    color: colors.text,
                    borderColor: colors.border,
                  },
                ]}
                placeholder="Password"
                placeholderTextColor={colors.textTertiary}
                value={password}
                onChangeText={setPassword}
                secureTextEntry
                autoCapitalize="none"
                autoComplete="password"
                textContentType="password"
              />
              {error ? (
                <Text style={[styles.errorText, { color: colors.error }]}>
                  {error}
                </Text>
              ) : null}
              <Pressable
                style={[
                  styles.primaryButton,
                  { backgroundColor: colors.primary },
                ]}
                onPress={handleEmailLogin}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator color={colors.textOnPrimary} />
                ) : (
                  <Text
                    style={[
                      styles.primaryButtonText,
                      { color: colors.textOnPrimary },
                    ]}
                  >
                    Sign in
                  </Text>
                )}
              </Pressable>

              <Pressable
                onPress={() =>
                  router.push({
                    pathname: "/(auth)/signup",
                    params: email.trim() ? { email: email.trim() } : {},
                  })
                }
                style={{ marginTop: Spacing.sm }}
              >
                <Text
                  style={[styles.linkText, { color: colors.primary }]}
                >
                  New here? Create an account
                </Text>
              </Pressable>

              <Pressable
                onPress={() =>
                  router.push({
                    pathname: "/(auth)/verify-email",
                    params: email.trim() ? { email: email.trim() } : {},
                  })
                }
              >
                <Text
                  style={[styles.linkText, { color: colors.textSecondary }]}
                >
                  I have a verification code
                </Text>
              </Pressable>

              <Pressable onPress={() => setShowEmailForm(false)}>
                <Text
                  style={[styles.linkText, { color: colors.textTertiary }]}
                >
                  Cancel
                </Text>
              </Pressable>
            </View>
          ) : (
            <Pressable
              style={[
                styles.googleButton,
                {
                  backgroundColor: colors.primary,
                  borderColor: colors.primary,
                  marginTop: Spacing.sm,
                },
              ]}
              onPress={() => setShowEmailForm(true)}
            >
              <Text
                style={[
                  styles.googleButtonText,
                  { color: colors.textOnPrimary },
                ]}
              >
                Continue with Email
              </Text>
            </Pressable>
          )}
        </View>

        <Text style={[styles.footer, { color: colors.textTertiary }]}>
          By signing in, you agree to help save lives 💉
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.xxxl,
  },
  heroSection: {
    alignItems: "center",
    marginBottom: Spacing.xxxl,
  },
  iconContainer: {
    width: 100,
    height: 100,
    borderRadius: 50,
    justifyContent: "center",
    alignItems: "center",
    marginBottom: Spacing.lg,
  },
  bloodIcon: {
    fontSize: 48,
  },
  appTitle: {
    fontSize: Typography.sizes.hero,
    fontWeight: "800",
    letterSpacing: -1,
    marginBottom: Spacing.sm,
  },
  appSubtitle: {
    fontSize: Typography.sizes.md,
    fontWeight: "400",
  },
  loginSection: {
    width: "100%",
    maxWidth: 360,
    alignItems: "center",
  },
  googleButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    paddingVertical: 14,
    borderRadius: Radius.md,
    borderWidth: 1,
    gap: Spacing.md,
  },
  googleIcon: {
    fontSize: 20,
    fontWeight: "700",
    color: "#4285F4",
  },
  googleButtonText: {
    fontSize: Typography.sizes.base,
    fontWeight: "600",
  },
  dividerRow: {
    flexDirection: "row",
    alignItems: "center",
    width: "100%",
    marginVertical: Spacing.lg,
    gap: Spacing.md,
  },
  dividerLine: {
    flex: 1,
    height: 1,
  },
  dividerText: {
    fontSize: Typography.sizes.sm,
  },
  emailSection: {
    width: "100%",
    gap: Spacing.md,
  },
  emailLabel: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
    textAlign: "center",
    marginBottom: Spacing.xs,
  },
  input: {
    width: "100%",
    paddingVertical: 12,
    paddingHorizontal: Spacing.base,
    borderRadius: Radius.md,
    borderWidth: 1,
    fontSize: Typography.sizes.base,
  },
  errorText: {
    fontSize: Typography.sizes.sm,
    textAlign: "center",
  },
  primaryButton: {
    width: "100%",
    paddingVertical: 14,
    borderRadius: Radius.md,
    alignItems: "center",
    justifyContent: "center",
    marginTop: Spacing.xs,
  },
  primaryButtonText: {
    fontSize: Typography.sizes.base,
    fontWeight: "700",
  },
  linkText: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
    textAlign: "center",
  },
  footer: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.xxxl,
    textAlign: "center",
  },
});
