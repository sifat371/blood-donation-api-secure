/**
 * Email/password registration.
 *
 * Only name, email and password are asked for here. Blood group, location and
 * date of birth are collected by the existing complete-profile screen, which
 * every account already passes through — duplicating them here would mean asking
 * twice and keeping two copies of the same validation.
 *
 * No session comes back from this screen. The account is created unverified and
 * unusable until the emailed code is entered, so the only place to go next is
 * the verification screen.
 */

import { signup } from "@/api/auth";
import { classifyError } from "@/api/errors";
import { Radius, Spacing, Typography, useThemeColors } from "@/theme";
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
import { useSafeAreaInsets } from "react-native-safe-area-context";

/**
 * Mirrors the backend's PASSWORD_MIN_LENGTH default. The backend is still the
 * authority — this only saves a round trip for an obviously short password.
 */
const PASSWORD_MIN_LENGTH = 8;

/** bcrypt silently ignores anything past 72 bytes, so the backend rejects it. */
const PASSWORD_MAX_BYTES = 72;

function byteLength(value: string): number {
  // Non-ASCII characters cost more than one byte, which is exactly the case a
  // character count would get wrong.
  let bytes = 0;
  for (const char of value) {
    const code = char.codePointAt(0) ?? 0;
    if (code <= 0x7f) bytes += 1;
    else if (code <= 0x7ff) bytes += 2;
    else if (code <= 0xffff) bytes += 3;
    else bytes += 4;
  }
  return bytes;
}

export default function SignupScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ email?: string }>();

  const [name, setName] = useState("");
  const [email, setEmail] = useState(params.email ?? "");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  /** Returns a message when something is wrong, or null when the form is fine. */
  const validate = (
    trimmedName: string,
    trimmedEmail: string,
  ): string | null => {
    if (!trimmedName) return "Enter your name.";
    if (!trimmedEmail) return "Enter your email address.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      return "That doesn't look like an email address.";
    }
    if (password.length < PASSWORD_MIN_LENGTH) {
      return `Your password must be at least ${PASSWORD_MIN_LENGTH} characters.`;
    }
    if (byteLength(password) > PASSWORD_MAX_BYTES) {
      return "That password is too long. Please choose a shorter one.";
    }
    if (password !== confirmPassword) return "The two passwords do not match.";
    return null;
  };

  const handleSignup = async () => {
    const trimmedName = name.trim();
    const trimmedEmail = email.trim();

    const problem = validate(trimmedName, trimmedEmail);
    if (problem) {
      setError(problem);
      return;
    }

    try {
      setLoading(true);
      setError("");

      const result = await signup({
        name: trimmedName,
        email: trimmedEmail,
        password,
      });

      // replace, not push: coming back to a filled-in signup form after the
      // account already exists would only produce a duplicate-email error.
      router.replace({
        pathname: "/(auth)/verify-email",
        params: {
          email: result.email,
          reason: "signup",
          delivery: result.delivery,
        },
      });
    } catch (err) {
      const failure = classifyError(err);

      // The address is taken. Signing in is the useful next step, not retrying.
      if (failure.kind === "ACCOUNT_EXISTS") {
        setError(failure.message);
        return;
      }

      setError(failure.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        style={{ flex: 1, backgroundColor: colors.background }}
        contentContainerStyle={[
          styles.container,
          { paddingBottom: Spacing.xl + insets.bottom + 32 },
        ]}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
      >
        <Text style={[styles.title, { color: colors.text }]}>
          Create your account
        </Text>
        <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
          We&apos;ll email you a code to confirm your address before you sign in.
        </Text>

        <Text style={[styles.label, { color: colors.text }]}>Full name *</Text>
        <TextInput
          style={[
            styles.input,
            {
              backgroundColor: colors.surfaceVariant,
              color: colors.text,
              borderColor: colors.border,
            },
          ]}
          placeholder="Your name"
          placeholderTextColor={colors.textTertiary}
          value={name}
          onChangeText={setName}
          autoCapitalize="words"
          autoComplete="name"
          textContentType="name"
        />

        <Text style={[styles.label, { color: colors.text }]}>Email *</Text>
        <TextInput
          style={[
            styles.input,
            {
              backgroundColor: colors.surfaceVariant,
              color: colors.text,
              borderColor: colors.border,
            },
          ]}
          placeholder="you@example.com"
          placeholderTextColor={colors.textTertiary}
          value={email}
          onChangeText={setEmail}
          keyboardType="email-address"
          autoCapitalize="none"
          autoComplete="email"
          textContentType="emailAddress"
        />

        <Text style={[styles.label, { color: colors.text }]}>Password *</Text>
        <TextInput
          style={[
            styles.input,
            {
              backgroundColor: colors.surfaceVariant,
              color: colors.text,
              borderColor: colors.border,
            },
          ]}
          placeholder={`At least ${PASSWORD_MIN_LENGTH} characters`}
          placeholderTextColor={colors.textTertiary}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoCapitalize="none"
          autoComplete="new-password"
          textContentType="newPassword"
        />

        <Text style={[styles.label, { color: colors.text }]}>
          Confirm password *
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
          placeholder="Re-enter your password"
          placeholderTextColor={colors.textTertiary}
          value={confirmPassword}
          onChangeText={setConfirmPassword}
          secureTextEntry
          autoCapitalize="none"
          autoComplete="new-password"
          textContentType="newPassword"
        />

        {error ? (
          <Text style={[styles.errorText, { color: colors.error }]}>
            {error}
          </Text>
        ) : null}

        <Pressable
          style={[styles.submitButton, { backgroundColor: colors.primary }]}
          onPress={handleSignup}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color={colors.textOnPrimary} />
          ) : (
            <Text style={[styles.submitText, { color: colors.textOnPrimary }]}>
              Create account
            </Text>
          )}
        </Pressable>

        <View style={styles.linkRow}>
          <Pressable onPress={() => router.back()}>
            <Text style={[styles.linkText, { color: colors.primary }]}>
              Already have an account? Sign in
            </Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    padding: Spacing.xl,
    paddingTop: 60,
  },
  title: {
    fontSize: Typography.sizes.xxl,
    fontWeight: "800",
    marginBottom: Spacing.sm,
  },
  subtitle: {
    fontSize: Typography.sizes.base,
    marginBottom: Spacing.lg,
    lineHeight: 22,
  },
  label: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
    marginBottom: Spacing.sm,
    marginTop: Spacing.base,
  },
  input: {
    paddingVertical: 12,
    paddingHorizontal: Spacing.base,
    borderRadius: Radius.md,
    borderWidth: 1,
    fontSize: Typography.sizes.base,
  },
  errorText: {
    fontSize: Typography.sizes.sm,
    textAlign: "center",
    marginTop: Spacing.md,
  },
  submitButton: {
    paddingVertical: 16,
    borderRadius: Radius.md,
    alignItems: "center",
    marginTop: Spacing.xl,
  },
  submitText: {
    fontSize: Typography.sizes.base,
    fontWeight: "700",
  },
  linkRow: {
    alignItems: "center",
    marginTop: Spacing.lg,
  },
  linkText: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
    textAlign: "center",
  },
});
