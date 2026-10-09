/**
 * Email verification — enter the code that was mailed out.
 *
 * The code is checked by the backend and nowhere else. This screen never sets a
 * verified flag itself; all it can do is submit what the user typed and report
 * the answer. On success there is still no session, because verification and
 * authentication are separate steps — so the user is sent back to sign in.
 */

import { resendVerification, verifyEmail } from "@/api/auth";
import { classifyError } from "@/api/errors";
import { Radius, Spacing, Typography, useThemeColors } from "@/theme";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
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

/** Matches the backend's EMAIL_VERIFICATION_CODE_DIGITS default. */
const CODE_LENGTH = 6;

/**
 * Matches EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS. Shown as a countdown so
 * the user waits instead of tapping into a 429 — the backend enforces it either
 * way, this just makes the wait visible.
 */
const RESEND_COOLDOWN_SECONDS = 60;

export default function VerifyEmailScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const params = useLocalSearchParams<{
    email?: string;
    reason?: string;
    delivery?: string;
  }>();

  const [email, setEmail] = useState(params.email ?? "");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [snackbarMessage, setSnackbarMessage] = useState("");
  const [snackbarVisible, setSnackbarVisible] = useState(false);
  // Non-zero means the resend button is on cooldown.
  const [cooldown, setCooldown] = useState(
    params.reason === "signup" ? RESEND_COOLDOWN_SECONDS : 0,
  );
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // One interval for the whole screen, cleared on unmount so a countdown that
  // is still running cannot tick against an unmounted component.
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setCooldown((current) => (current > 0 ? current - 1 : 0));
    }, 1000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const showSnackbar = (message: string) => {
    setSnackbarMessage(message);
    setSnackbarVisible(true);
  };

  const handleVerify = async () => {
    const trimmedEmail = email.trim();
    const trimmedCode = code.trim();

    if (!trimmedEmail) {
      setError("Enter the email address you signed up with.");
      return;
    }
    if (trimmedCode.length !== CODE_LENGTH) {
      setError(`Enter the ${CODE_LENGTH}-digit code from your email.`);
      return;
    }

    try {
      setLoading(true);
      setError("");

      const result = await verifyEmail(trimmedEmail, trimmedCode);
      if (!result.email_verified) {
        // Should not happen — the backend only returns 200 once verified — but
        // never claim success on the strength of a status code alone.
        setError("That code could not be confirmed. Please try again.");
        return;
      }

      router.replace({
        pathname: "/(auth)/login",
        params: { email: trimmedEmail, verified: "1" },
      });
    } catch (err) {
      const failure = classifyError(err);

      // The backend distinguishes wrong, expired and exhausted. Each needs a
      // different next move, and the code the user has may now be useless.
      if (failure.code === "VERIFICATION_CODE_EXPIRED") {
        setError("That code has expired. Tap “Resend code” to get a new one.");
        setCode("");
      } else if (failure.code === "VERIFICATION_ATTEMPTS_EXCEEDED") {
        setError(
          "Too many incorrect attempts. Tap “Resend code” to get a new one.",
        );
        setCode("");
      } else {
        setError(failure.message);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setError("Enter the email address you signed up with.");
      return;
    }
    if (cooldown > 0) return;

    try {
      setResending(true);
      setError("");

      const result = await resendVerification(trimmedEmail);
      setCode("");
      setCooldown(RESEND_COOLDOWN_SECONDS);
      showSnackbar(result.message);
    } catch (err) {
      const failure = classifyError(err);

      // A 429 here means the server-side cooldown is still running. Start the
      // local countdown so the button reflects reality.
      if (failure.code === "RESEND_COOLDOWN" || failure.kind === "RATE_LIMITED") {
        setCooldown(RESEND_COOLDOWN_SECONDS);
        setError("Please wait a moment before requesting another code.");
      } else {
        setError(failure.message);
      }
    } finally {
      setResending(false);
    }
  };

  const resendDisabled = resending || cooldown > 0;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        style={{ flex: 1, backgroundColor: colors.background }}
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
      >
        <Snackbar
          visible={snackbarVisible}
          onDismiss={() => setSnackbarVisible(false)}
          duration={4000}
          action={{ label: "Close", onPress: () => setSnackbarVisible(false) }}
        >
          {snackbarMessage}
        </Snackbar>

        <Text style={[styles.title, { color: colors.text }]}>
          Verify your email
        </Text>
        <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
          {params.reason === "login"
            ? "Your account isn't active yet. Enter the code we emailed you to finish setting it up."
            : `We sent a ${CODE_LENGTH}-digit code to your email address. Enter it below to activate your account.`}
        </Text>

        <Text style={[styles.label, { color: colors.text }]}>Email</Text>
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

        <Text style={[styles.label, { color: colors.text }]}>
          Verification code
        </Text>
        <TextInput
          style={[
            styles.input,
            styles.codeInput,
            {
              backgroundColor: colors.surfaceVariant,
              color: colors.text,
              borderColor: colors.border,
            },
          ]}
          placeholder="000000"
          placeholderTextColor={colors.textTertiary}
          value={code}
          // Digits only, so a pasted "Code: 123456" cannot be submitted verbatim.
          onChangeText={(value) =>
            setCode(value.replace(/\D/g, "").slice(0, CODE_LENGTH))
          }
          keyboardType="number-pad"
          maxLength={CODE_LENGTH}
          autoComplete="sms-otp"
          textContentType="oneTimeCode"
        />

        {error ? (
          <Text style={[styles.errorText, { color: colors.error }]}>
            {error}
          </Text>
        ) : null}

        <Pressable
          style={[styles.submitButton, { backgroundColor: colors.primary }]}
          onPress={handleVerify}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color={colors.textOnPrimary} />
          ) : (
            <Text style={[styles.submitText, { color: colors.textOnPrimary }]}>
              Verify email
            </Text>
          )}
        </Pressable>

        <Pressable
          style={[
            styles.secondaryButton,
            {
              borderColor: resendDisabled ? colors.border : colors.primary,
            },
          ]}
          onPress={handleResend}
          disabled={resendDisabled}
        >
          {resending ? (
            <ActivityIndicator color={colors.primary} />
          ) : (
            <Text
              style={[
                styles.secondaryText,
                { color: resendDisabled ? colors.textTertiary : colors.primary },
              ]}
            >
              {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}
            </Text>
          )}
        </Pressable>

        <View style={styles.linkRow}>
          <Pressable
            onPress={() =>
              router.replace({
                pathname: "/(auth)/login",
                params: email.trim() ? { email: email.trim() } : {},
              })
            }
          >
            <Text style={[styles.linkText, { color: colors.textSecondary }]}>
              Back to sign in
            </Text>
          </Pressable>
        </View>

        {params.delivery === "outbox" ? (
          <Text style={[styles.devNote, { color: colors.textTertiary }]}>
            This server is writing mail to its local outbox instead of sending
            it. Ask whoever runs the server for the code.
          </Text>
        ) : null}
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
  codeInput: {
    fontSize: Typography.sizes.xl,
    letterSpacing: 8,
    textAlign: "center",
    fontWeight: "700",
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
  secondaryButton: {
    paddingVertical: 14,
    borderRadius: Radius.md,
    borderWidth: 1,
    alignItems: "center",
    marginTop: Spacing.md,
  },
  secondaryText: {
    fontSize: Typography.sizes.base,
    fontWeight: "600",
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
  devNote: {
    fontSize: Typography.sizes.xs,
    textAlign: "center",
    marginTop: Spacing.xl,
    lineHeight: 18,
  },
});
