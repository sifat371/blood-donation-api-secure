/**
 * Profile completion screen — shown after first Google sign-in.
 */
import { completeProfile, registerFcmToken } from "@/api/profile";
import { classifyError } from "@/api/errors";
import { bdGeoLocations, divisions } from "@/constants/bangladeshGeo";
import { useAuthStore } from "@/store/auth-store";
import { useLocationStore } from "@/store/location-store";
import { useNotificationStore } from "@/store/notification-store";
import { Radius, Spacing, Typography, useThemeColors } from "@/theme";
import DateTimePicker, {
  DateTimePickerEvent,
} from "@react-native-community/datetimepicker";
import { Picker } from "@react-native-picker/picker";
import * as Location from "expo-location";
import { useRouter } from "expo-router";
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

const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];
const GENDERS = ["Male", "Female", "Other"];

/** Turn a failed save into something the user can act on. */
function describeProfileError(err: unknown): string {
  const failure = classifyError(err);
  return failure.kind === "UNKNOWN_ERROR"
    ? "Failed to complete profile. Please try again."
    : failure.message;
}

// Minimum age required to register as a donor/user.
const MIN_AGE = 15;

// Latest allowed date of birth (i.e. the user must be born on or before this
// date to be at least MIN_AGE years old today).
const getMaxDob = () => {
  const today = new Date();
  return new Date(
    today.getFullYear() - MIN_AGE,
    today.getMonth(),
    today.getDate(),
  );
};

// Format a Date to a YYYY-MM-DD string using local date parts (avoids the
// timezone shift that toISOString() can introduce).
const formatDate = (d: Date) => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

// Compute full years of age from a date of birth.
const calcAge = (d: Date) => {
  const today = new Date();
  let age = today.getFullYear() - d.getFullYear();
  const monthDiff = today.getMonth() - d.getMonth();
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < d.getDate())) {
    age -= 1;
  }
  return age;
};

export default function CompleteProfileScreen() {
  const colors = useThemeColors();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const setUser = useAuthStore((s) => s.setUser);
  const setProfileComplete = useAuthStore((s) => s.setProfileComplete);

  const [phone, setPhone] = useState("");
  const [bloodGroup, setBloodGroup] = useState("");
  const [division, setDivision] = useState("");
  const [district, setDistrict] = useState("");
  const [upazila, setUpazila] = useState("");
  const [gender, setGender] = useState("");
  const [dob, setDob] = useState("");
  const [dobDate, setDobDate] = useState<Date | null>(null);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const maxDob = getMaxDob();
  const setLocation = useLocationStore((s) => s.setLocation);
  const latitude = useLocationStore((s) => s.latitude);
  const longitude = useLocationStore((s) => s.longitude);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [locationLoading, setLocationLoading] = useState(false);

  // Derived lists based on selection

  const districtsList = division ? Object.keys(bdGeoLocations[division]) : [];
  const upazilasList =
    division && district ? bdGeoLocations[division][district] : [];

  const fetchLocation = async () => {
    setLocationLoading(true);
    setError("");
    try {
      let { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") {
        setError("Permission to access location was denied.");
        setLocationLoading(false);
        return;
      }

      let currentLocation = await Location.getCurrentPositionAsync({});
      setLocation(
        currentLocation.coords.latitude,
        currentLocation.coords.longitude,
      );
    } catch (e) {
      setError("Failed to fetch location. Ensure GPS is turned on.");
    } finally {
      setLocationLoading(false);
    }
  };
  // Cascading Handlers
  const handleDivisionChange = (selectedDivision: string) => {
    setDivision(selectedDivision);
    setDistrict(""); // Reset dependent fields
    setUpazila("");
  };

  const handleDistrictChange = (selectedDistrict: string) => {
    setDistrict(selectedDistrict);
    setUpazila(""); // Reset dependent field
  };

  const handleDobChange = (event: DateTimePickerEvent, selected?: Date) => {
    // On Android the picker is a modal dialog that must be closed manually.
    setShowDatePicker(Platform.OS === "ios");
    if (event.type === "dismissed" || !selected) {
      return;
    }
    if (calcAge(selected) < MIN_AGE) {
      setError(`You must be at least ${MIN_AGE} years old to register.`);
      return;
    }
    setError("");
    setDobDate(selected);
    setDob(formatDate(selected));
  };

  const handleSubmit = async () => {
    if (
      !phone ||
      !bloodGroup ||
      !division ||
      !district ||
      !upazila ||
      !gender ||
      !dob
    ) {
      setError("Please fill in all required fields");
      return;
    }
    if (!dobDate || calcAge(dobDate) < MIN_AGE) {
      setError(`You must be at least ${MIN_AGE} years old to register.`);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const profile = await completeProfile({
        phone,
        blood_group: bloodGroup,
        division,
        district,
        upazila,
        gender,
        date_of_birth: dob,
        latitude: useLocationStore.getState().latitude || null,
        longitude: useLocationStore.getState().longitude || null,
      });

      // Push registration is a separate endpoint because one account can have
      // several devices. Best-effort: failing to register push must not block
      // the user from finishing sign-up.
      const pushToken = useNotificationStore.getState().pushToken;
      if (pushToken) {
        try {
          await registerFcmToken(pushToken);
        } catch (pushError) {
          console.warn("Could not register push token:", pushError);
        }
      }

      setUser(profile);
      setProfileComplete(true);
      router.replace("/(main)");
    } catch (e: any) {
      setError(describeProfileError(e));
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
          Complete Your Profile
        </Text>
        <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
          We need a few details to help match you with donors and requests.
        </Text>

        {/* Blood Group Picker */}
        <Text style={[styles.label, { color: colors.text }]}>
          Blood Group *
        </Text>
        <View style={styles.chipRow}>
          {BLOOD_GROUPS.map((bg) => (
            <Pressable
              key={bg}
              style={[
                styles.chip,
                {
                  backgroundColor:
                    bloodGroup === bg ? colors.primary : colors.surfaceVariant,
                  borderColor:
                    bloodGroup === bg ? colors.primary : colors.border,
                },
              ]}
              onPress={() => setBloodGroup(bg)}
            >
              <Text
                style={[
                  styles.chipText,
                  {
                    color:
                      bloodGroup === bg ? colors.textOnPrimary : colors.text,
                  },
                ]}
              >
                {bg}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Gender */}
        <Text style={[styles.label, { color: colors.text }]}>Gender *</Text>
        <View style={styles.chipRow}>
          {GENDERS.map((g) => (
            <Pressable
              key={g}
              style={[
                styles.chip,
                {
                  backgroundColor:
                    gender === g ? colors.primary : colors.surfaceVariant,
                  borderColor: gender === g ? colors.primary : colors.border,
                },
              ]}
              onPress={() => setGender(g)}
            >
              <Text
                style={[
                  styles.chipText,
                  { color: gender === g ? colors.textOnPrimary : colors.text },
                ]}
              >
                {g}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Text inputs */}
        <Text style={[styles.label, { color: colors.text }]}>
          Phone Number *
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
          placeholder="+880XXXXXXXXXX"
          placeholderTextColor={colors.textTertiary}
          value={phone}
          onChangeText={setPhone}
          keyboardType="phone-pad"
        />

        <Text style={[styles.label, { color: colors.text }]}>
          Date of Birth *
        </Text>
        <Pressable
          style={[
            styles.input,
            styles.dateInput,
            {
              backgroundColor: colors.surfaceVariant,
              borderColor: colors.border,
            },
          ]}
          onPress={() => setShowDatePicker(true)}
        >
          <Text
            style={{
              color: dob ? colors.text : colors.textTertiary,
              fontSize: Typography.sizes.base,
            }}
          >
            {dob || "Select your date of birth"}
          </Text>
        </Pressable>
        <Text style={[styles.helperText, { color: colors.textSecondary }]}>
          You must be at least {MIN_AGE} years old.
        </Text>
        {showDatePicker && (
          <DateTimePicker
            value={dobDate ?? maxDob}
            mode="date"
            display={Platform.OS === "ios" ? "spinner" : "default"}
            maximumDate={maxDob}
            onChange={handleDobChange}
          />
        )}

        <Text style={[styles.label, { color: colors.text }]}>Division *</Text>
        <View
          style={[
            styles.pickerContainer,
            {
              backgroundColor: colors.surfaceVariant,
              borderColor: colors.border,
            },
          ]}
        >
          <Picker
            selectedValue={division}
            onValueChange={handleDivisionChange}
            style={{ color: colors.text }}
          >
            <Picker.Item
              label="Select Division"
              value=""
              color={colors.textTertiary}
            />
            {divisions.map((div) => (
              <Picker.Item key={div} label={div} value={div} />
            ))}
          </Picker>
        </View>

        <Text style={[styles.label, { color: colors.text }]}>District *</Text>
        <View
          style={[
            styles.pickerContainer,
            {
              backgroundColor: colors.surfaceVariant,
              borderColor: colors.border,
            },
          ]}
        >
          <Picker
            selectedValue={district}
            onValueChange={handleDistrictChange}
            enabled={!!division}
            style={{ color: colors.text }}
          >
            <Picker.Item
              label="Select District"
              value=""
              color={colors.textTertiary}
            />
            {districtsList.map((dist) => (
              <Picker.Item key={dist} label={dist} value={dist} />
            ))}
          </Picker>
        </View>
        <Text style={[styles.label, { color: colors.text }]}>Upazila *</Text>
        <View
          style={[
            styles.pickerContainer,
            {
              backgroundColor: colors.surfaceVariant,
              borderColor: colors.border,
            },
          ]}
        >
          <Picker
            selectedValue={upazila}
            onValueChange={setUpazila}
            enabled={!!district}
            style={{ color: colors.text }}
          >
            <Picker.Item
              label="Select Upazila"
              value=""
              color={colors.textTertiary}
            />
            {upazilasList.map((upa) => (
              <Picker.Item key={upa} label={upa} value={upa} />
            ))}
          </Picker>
        </View>

        <Text
          style={[styles.label, { color: colors.text, marginTop: Spacing.xl }]}
        >
          Current GPS Location (Optional for better matching)
        </Text>

        <Pressable
          style={[
            styles.locationBtn,
            {
              backgroundColor: colors.surfaceVariant,
              borderColor: colors.primary,
              paddingVertical: Spacing.sm,
              paddingHorizontal: Spacing.base,
              borderRadius: Radius.md,
              alignItems: "center",
              marginTop: Spacing.sm,
            },
          ]}
          onPress={fetchLocation}
        >
          {locationLoading ? (
            <ActivityIndicator color={colors.primary} />
          ) : (
            <Text
              style={{
                color: colors.primary,
                fontWeight: "600",
                fontSize: Typography.sizes.md,
              }}
            >
              {latitude != null && longitude != null
                ? `Location: ${latitude.toFixed(4)}, ${longitude.toFixed(4)}`
                : "Fetch Current Location"}
            </Text>
          )}
        </Pressable>

        {error ? (
          <Text style={[styles.errorText, { color: colors.error }]}>
            {error}
          </Text>
        ) : null}

        <Pressable
          style={[styles.submitButton, { backgroundColor: colors.primary }]}
          onPress={handleSubmit}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color={colors.textOnPrimary} />
          ) : (
            <Text style={[styles.submitText, { color: colors.textOnPrimary }]}>
              Complete Profile
            </Text>
          )}
        </Pressable>
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
    marginBottom: Spacing.xl,
    lineHeight: 22,
  },
  label: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
    marginBottom: Spacing.sm,
    marginTop: Spacing.base,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.sm,
  },
  chip: {
    paddingHorizontal: Spacing.base,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.full,
    borderWidth: 1,
  },
  chipText: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
  },
  input: {
    paddingVertical: 12,
    paddingHorizontal: Spacing.base,
    borderRadius: Radius.md,
    borderWidth: 1,
    fontSize: Typography.sizes.base,
  },
  dateInput: {
    justifyContent: "center",
  },
  // Wraps each <Picker>. Was referenced by three pickers but never defined, so
  // the Division/District/Upazila dropdowns rendered with no border or
  // background and looked like plain text.
  pickerContainer: {
    borderRadius: Radius.md,
    borderWidth: 1,
    overflow: "hidden",
    justifyContent: "center",
  },
  locationBtn: {
    borderWidth: 1,
  },
  helperText: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.xs,
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
});
