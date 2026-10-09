/**
 * Create blood request screen — form to submit a new blood request.
 */

import {
  MAX_REQUEST_UNITS,
  MIN_REQUEST_UNITS,
  createBloodRequest,
} from "@/api/blood-requests";
import { classifyError } from "@/api/errors";
import { bdMedCLG } from "@/constants/bdMed";
import { useAuthStore } from "@/store/auth-store";
import { useLocationStore } from "@/store/location-store";
import { Radius, Spacing, Typography, useThemeColors } from "@/theme";
import { Picker } from "@react-native-picker/picker";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import DateTimePicker from "@react-native-community/datetimepicker";
import { useSafeAreaInsets } from "react-native-safe-area-context";

const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];

function describeError(err: unknown): string {
  const failure = classifyError(err);
  return failure.kind === "UNKNOWN_ERROR"
    ? "Failed to create request"
    : failure.message;
}

export default function CreateRequestScreen() {
  const insets = useSafeAreaInsets();
  const colors = useThemeColors();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const gpsLatitude = useLocationStore((s) => s.latitude);
  const gpsLongitude = useLocationStore((s) => s.longitude);

  const [patientName, setPatientName] = useState("");
  const [bloodGroup, setBloodGroup] = useState("");
  const [units, setUnits] = useState("1");
  const [hospitalName, setHospitalName] = useState("");
  const [hospitalAddress, setHospitalAddress] = useState("");
  const [neededDate, setNeededDate] = useState("");
  const [contactNumber, setContactNumber] = useState(user?.phone || "");
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [dateValue, setDateValue] = useState(new Date());

  /**
   * Where the request is anchored: live GPS first, the profile's saved
   * coordinates second.
   *
   * This matters more than it looks. The backend notifies nearby donors by
   * measuring distance from the request's coordinates, so a request with no
   * location reaches nobody — it just sits in the requester's list. The banner
   * below says so rather than letting that fail quietly.
   */
  const latitude = gpsLatitude ?? user?.latitude ?? null;
  const longitude = gpsLongitude ?? user?.longitude ?? null;
  const hasLocation = latitude != null && longitude != null;

  /** Digits only, so the field can never hold something unparseable. */
  const handleUnitsChange = (text: string) => {
    setUnits(text.replace(/[^0-9]/g, ""));
  };

  const handleSubmit = async () => {
    if (
      !patientName ||
      !bloodGroup ||
      !hospitalName ||
      !neededDate ||
      !contactNumber
    ) {
      setError("Please fill in all required fields");
      return;
    }

    // Check the units rule here instead of letting the server reject it. The old
    // code sent `parseInt(units) || 1`, which turned "0" into 1 without saying
    // so and passed "50" through to a 422 the user couldn't interpret.
    const unitsValue = Number.parseInt(units, 10);
    if (
      !Number.isInteger(unitsValue) ||
      unitsValue < MIN_REQUEST_UNITS ||
      unitsValue > MAX_REQUEST_UNITS
    ) {
      setError(
        `Units must be a whole number between ${MIN_REQUEST_UNITS} and ${MAX_REQUEST_UNITS}.`,
      );
      return;
    }

    setLoading(true);
    setError("");
    try {
      await createBloodRequest({
        patient_name: patientName,
        blood_group: bloodGroup,
        units: unitsValue,
        hospital_name: hospitalName,
        hospital_address: hospitalAddress || undefined,
        latitude: latitude ?? undefined,
        longitude: longitude ?? undefined,
        needed_date: neededDate,
        contact_number: contactNumber,
        notes: notes || undefined,
      });
      Alert.alert(
        "Request Created!",
        hasLocation
          ? "Your blood request has been created. Nearby donors will be notified."
          : "Your blood request has been created. Because no location was attached, nearby donors were not alerted — add your location in your profile so donors can find it.",
        [{ text: "OK", onPress: () => router.back() }],
      );
    } catch (e) {
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  };
  const onDateChange = (event: any, selectedDate?: Date) => {
    // If user cancels the picker on Android, selectedDate is undefined
    if (event.type === "dismissed") {
      setShowDatePicker(false);
      return;
    }

    if (selectedDate) {
      setDateValue(selectedDate);

      // Backend expects an ISO date string: YYYY-MM-DD
      const year = selectedDate.getFullYear();
      const month = String(selectedDate.getMonth() + 1).padStart(2, "0");
      const day = String(selectedDate.getDate()).padStart(2, "0");
      const formattedDate = `${year}-${month}-${day}`;

      setNeededDate(formattedDate);
    }

    // Hide the picker on Android after selection (iOS handles this differently depending on display mode)
    if (Platform.OS === "android") {
      setShowDatePicker(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Stack.Screen
        options={{
          headerShown: true,
          title: "Create Request",
          headerTintColor: colors.text,
          headerStyle: { backgroundColor: colors.surface },
        }}
      />
      <ScrollView
        style={{ flex: 1, backgroundColor: colors.background }}
        contentContainerStyle={[
          styles.container,
          { paddingBottom: Spacing.xl + insets.bottom + 128 },
        ]}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
      >
        <Text style={[styles.title, { color: colors.text }]}>
          New Blood Request
        </Text>
        <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
          Fill in the details to request blood donors. Nearby eligible donors
          will be notified.
        </Text>

        {/* Donor alerts are distance-based, so a request without coordinates is
            visible only to its owner. Say that up front, before the form is
            filled in. */}
        {!hasLocation && (
          <Text
            style={[
              styles.warningBanner,
              { color: colors.error, backgroundColor: colors.errorLight },
            ]}
          >
            📍 No location saved for your account. Donors are alerted by
            distance, so this request won&apos;t reach anyone until you add your
            location from the Profile tab.
          </Text>
        )}

        {/* Blood Group */}
        <Text style={[styles.label, { color: colors.text }]}>
          Blood Group Needed *
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

        <Text style={[styles.label, { color: colors.text }]}>
          Patient Name *
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
          placeholder="Full name of the patient"
          placeholderTextColor={colors.textTertiary}
          value={patientName}
          onChangeText={setPatientName}
        />

        <Text style={[styles.label, { color: colors.text }]}>
          Units Needed *
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
          placeholder="1"
          placeholderTextColor={colors.textTertiary}
          value={units}
          onChangeText={handleUnitsChange}
          keyboardType="number-pad"
          maxLength={2}
        />
        <Text style={[styles.hint, { color: colors.textTertiary }]}>
          Between {MIN_REQUEST_UNITS} and {MAX_REQUEST_UNITS} units per request.
        </Text>

        <Text style={[styles.label, { color: colors.text }]}>
          Hospital Name *
        </Text>
        <Picker
          selectedValue={hospitalName}
          onValueChange={(itemValue) => {
            setHospitalName(itemValue);
            const selectedHospital = bdMedCLG.find(
              (hospital) => hospital.name === itemValue,
            );

            // 2. Set the actual local address, or reset it if nothing is found
            if (selectedHospital) {
              setHospitalAddress(selectedHospital.localAddress);
            } else {
              setHospitalAddress(""); // Resets if they select the placeholder "Select Hospital"
            }
          }}
        >
          <Picker.Item
            label="Select Hospital"
            value=""
            color={colors.textTertiary}
          />
          {bdMedCLG.map((div) => (
            <Picker.Item key={div.name} label={div.name} value={div.name} />
          ))}
        </Picker>

        <Text style={[styles.label, { color: colors.text }]}>
          Hospital Address
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
          placeholder="Full address"
          placeholderTextColor={colors.textTertiary}
          value={hospitalAddress}
          onChangeText={setHospitalAddress}
        />

        <Text style={[styles.label, { color: colors.text }]}>
          Date Needed * (YYYY-MM-DD)
        </Text>
        <Pressable
          onPress={() => setShowDatePicker(true)}
          style={[
            styles.input,
            {
              backgroundColor: colors.surfaceVariant,
              borderColor: colors.border,
              justifyContent: "center",
              minHeight: 48, // Ensure it has a similar height to a TextInput
            },
          ]}
        >
          <Text
            style={{ color: neededDate ? colors.text : colors.textTertiary }}
          >
            {neededDate || "Select a date (e.g., 2026-07-10)"}
          </Text>
        </Pressable>
        {showDatePicker && (
          <DateTimePicker
            value={dateValue}
            mode="date"
            display={Platform.OS === "ios" ? "spinner" : "default"} // 'spinner' is often better for iOS inline
            onChange={onDateChange}
            minimumDate={new Date()} // Optional: Prevents users from selecting a date in the past
          />
        )}

        <Text style={[styles.label, { color: colors.text }]}>
          Contact Number *
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
          value={contactNumber}
          onChangeText={setContactNumber}
          keyboardType="phone-pad"
        />

        <Text style={[styles.label, { color: colors.text }]}>Notes</Text>
        <TextInput
          style={[
            styles.textArea,
            {
              backgroundColor: colors.surfaceVariant,
              color: colors.text,
              borderColor: colors.border,
            },
          ]}
          placeholder="Any additional information..."
          placeholderTextColor={colors.textTertiary}
          value={notes}
          onChangeText={setNotes}
          multiline
          numberOfLines={3}
        />

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
              Submit Blood Request
            </Text>
          )}
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: Spacing.xl,
    paddingBottom: 40,
  },
  title: {
    fontSize: Typography.sizes.xl,
    fontWeight: "800",
    marginBottom: Spacing.xs,
  },
  subtitle: {
    fontSize: Typography.sizes.sm,
    lineHeight: 20,
    marginBottom: Spacing.lg,
  },
  label: {
    fontSize: Typography.sizes.sm,
    fontWeight: "600",
    marginBottom: Spacing.sm,
    marginTop: Spacing.base,
  },
  hint: {
    fontSize: Typography.sizes.xs,
    marginTop: Spacing.xs,
  },
  warningBanner: {
    fontSize: Typography.sizes.sm,
    lineHeight: 20,
    padding: Spacing.md,
    borderRadius: Radius.md,
    marginBottom: Spacing.base,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.sm,
  },
  chip: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.full,
    borderWidth: 1,
  },
  chipText: {
    fontSize: Typography.sizes.sm,
    fontWeight: "700",
  },
  input: {
    paddingVertical: 12,
    paddingHorizontal: Spacing.base,
    borderRadius: Radius.md,
    borderWidth: 1,
    fontSize: Typography.sizes.base,
  },
  textArea: {
    paddingVertical: 12,
    paddingHorizontal: Spacing.base,
    borderRadius: Radius.md,
    borderWidth: 1,
    fontSize: Typography.sizes.base,
    minHeight: 80,
    textAlignVertical: "top",
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
