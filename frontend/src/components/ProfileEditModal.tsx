/**
 * Profile edit form, shown as a modal over the profile screen.
 *
 * Deliberately a modal rather than a new route: the profile tab is a leaf, and
 * turning it into a stack to hold one more screen would reshape the tab layout
 * for no user-visible gain.
 *
 * Only fields the user should be able to change are here. Identity (email,
 * google_id) and derived state (last_donation_date) are server-owned.
 */

import { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Picker } from '@react-native-picker/picker';
import { classifyError } from '@/api/errors';
import { bdGeoLocations, divisions } from '@/constants/bangladeshGeo';
import { Radius, Spacing, Typography, useThemeColors } from '@/theme';
import { ProfileUpdateData, UserProfile, updateProfile } from '@/api/profile';

const BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];
const GENDERS = ['Male', 'Female', 'Other'];

function describeError(err: unknown): string {
  const failure = classifyError(err);
  return failure.kind === 'UNKNOWN_ERROR'
    ? 'Could not save your changes. Please try again.'
    : failure.message;
}

export function ProfileEditModal({
  visible,
  profile,
  onClose,
  onSaved,
}: {
  visible: boolean;
  profile: UserProfile;
  onClose: () => void;
  onSaved: (updated: UserProfile) => void;
}) {
  const colors = useThemeColors();

  const [name, setName] = useState(profile.name ?? '');
  const [phone, setPhone] = useState(profile.phone ?? '');
  const [bloodGroup, setBloodGroup] = useState(profile.blood_group ?? '');
  const [gender, setGender] = useState(profile.gender ?? '');
  const [division, setDivision] = useState(profile.division ?? '');
  const [district, setDistrict] = useState(profile.district ?? '');
  const [upazila, setUpazila] = useState(profile.upazila ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Guard against a stored division that isn't in the lookup table.
  const districtsList =
    division && bdGeoLocations[division] ? Object.keys(bdGeoLocations[division]) : [];
  const upazilasList =
    division && district && bdGeoLocations[division]?.[district]
      ? bdGeoLocations[division][district]
      : [];

  const handleDivisionChange = (value: string) => {
    setDivision(value);
    setDistrict('');
    setUpazila('');
  };

  const handleDistrictChange = (value: string) => {
    setDistrict(value);
    setUpazila('');
  };

  const handleSave = async () => {
    if (!name.trim()) {
      setError('Name cannot be empty.');
      return;
    }

    // PATCH semantics: send only what actually changed. Sending unchanged values
    // is harmless but makes the audit log noisier and the request larger.
    const updates: ProfileUpdateData = {};
    if (name.trim() !== profile.name) updates.name = name.trim();
    if (phone.trim() !== (profile.phone ?? '')) updates.phone = phone.trim();
    if (bloodGroup !== (profile.blood_group ?? '')) updates.blood_group = bloodGroup;
    if (gender !== (profile.gender ?? '')) updates.gender = gender;
    if (division !== (profile.division ?? '')) updates.division = division;
    if (district !== (profile.district ?? '')) updates.district = district;
    if (upazila !== (profile.upazila ?? '')) updates.upazila = upazila;

    if (Object.keys(updates).length === 0) {
      onClose();
      return;
    }

    setSaving(true);
    setError('');
    try {
      const updated = await updateProfile(updates);
      onSaved(updated);
      onClose();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        style={{ flex: 1, backgroundColor: colors.background }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={[styles.header, { borderBottomColor: colors.border }]}>
          <Pressable onPress={onClose} hitSlop={12}>
            <Text style={[styles.headerAction, { color: colors.textSecondary }]}>Cancel</Text>
          </Pressable>
          <Text style={[styles.headerTitle, { color: colors.text }]}>Edit Profile</Text>
          <Pressable onPress={handleSave} disabled={saving} hitSlop={12}>
            {saving ? (
              <ActivityIndicator size="small" color={colors.primary} />
            ) : (
              <Text style={[styles.headerAction, { color: colors.primary, fontWeight: '700' }]}>
                Save
              </Text>
            )}
          </Pressable>
        </View>

        <ScrollView
          contentContainerStyle={styles.body}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={[styles.label, { color: colors.text }]}>Name</Text>
          <TextInput
            style={[
              styles.input,
              {
                backgroundColor: colors.surfaceVariant,
                color: colors.text,
                borderColor: colors.border,
              },
            ]}
            value={name}
            onChangeText={setName}
            placeholder="Your name"
            placeholderTextColor={colors.textTertiary}
          />

          <Text style={[styles.label, { color: colors.text }]}>Phone</Text>
          <TextInput
            style={[
              styles.input,
              {
                backgroundColor: colors.surfaceVariant,
                color: colors.text,
                borderColor: colors.border,
              },
            ]}
            value={phone}
            onChangeText={setPhone}
            placeholder="+880XXXXXXXXXX"
            placeholderTextColor={colors.textTertiary}
            keyboardType="phone-pad"
          />

          <Text style={[styles.label, { color: colors.text }]}>Blood Group</Text>
          <View style={styles.chipRow}>
            {BLOOD_GROUPS.map((bg) => (
              <Pressable
                key={bg}
                onPress={() => setBloodGroup(bg)}
                style={[
                  styles.chip,
                  {
                    backgroundColor: bloodGroup === bg ? colors.primary : colors.surfaceVariant,
                    borderColor: bloodGroup === bg ? colors.primary : colors.border,
                  },
                ]}
              >
                <Text
                  style={[
                    styles.chipText,
                    { color: bloodGroup === bg ? colors.textOnPrimary : colors.text },
                  ]}
                >
                  {bg}
                </Text>
              </Pressable>
            ))}
          </View>

          <Text style={[styles.label, { color: colors.text }]}>Gender</Text>
          <View style={styles.chipRow}>
            {GENDERS.map((g) => (
              <Pressable
                key={g}
                onPress={() => setGender(g)}
                style={[
                  styles.chip,
                  {
                    backgroundColor: gender === g ? colors.primary : colors.surfaceVariant,
                    borderColor: gender === g ? colors.primary : colors.border,
                  },
                ]}
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

          <Text style={[styles.label, { color: colors.text }]}>Division</Text>
          <View
            style={[
              styles.pickerContainer,
              { backgroundColor: colors.surfaceVariant, borderColor: colors.border },
            ]}
          >
            <Picker
              selectedValue={division}
              onValueChange={handleDivisionChange}
              style={{ color: colors.text }}
            >
              <Picker.Item label="Select Division" value="" color={colors.textTertiary} />
              {divisions.map((div) => (
                <Picker.Item key={div} label={div} value={div} />
              ))}
            </Picker>
          </View>

          <Text style={[styles.label, { color: colors.text }]}>District</Text>
          <View
            style={[
              styles.pickerContainer,
              { backgroundColor: colors.surfaceVariant, borderColor: colors.border },
            ]}
          >
            <Picker
              selectedValue={district}
              onValueChange={handleDistrictChange}
              enabled={districtsList.length > 0}
              style={{ color: colors.text }}
            >
              <Picker.Item label="Select District" value="" color={colors.textTertiary} />
              {districtsList.map((d) => (
                <Picker.Item key={d} label={d} value={d} />
              ))}
            </Picker>
          </View>

          <Text style={[styles.label, { color: colors.text }]}>Upazila</Text>
          <View
            style={[
              styles.pickerContainer,
              { backgroundColor: colors.surfaceVariant, borderColor: colors.border },
            ]}
          >
            <Picker
              selectedValue={upazila}
              onValueChange={setUpazila}
              enabled={upazilasList.length > 0}
              style={{ color: colors.text }}
            >
              <Picker.Item label="Select Upazila" value="" color={colors.textTertiary} />
              {upazilasList.map((u) => (
                <Picker.Item key={u} label={u} value={u} />
              ))}
            </Picker>
          </View>

          {error ? (
            <Text style={[styles.errorText, { color: colors.error }]}>{error}</Text>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.xl,
    paddingTop: 60,
    paddingBottom: Spacing.md,
    borderBottomWidth: 1,
  },
  headerTitle: {
    fontSize: Typography.sizes.md,
    fontWeight: '800',
  },
  headerAction: {
    fontSize: Typography.sizes.base,
  },
  body: {
    padding: Spacing.xl,
    paddingBottom: 60,
  },
  label: {
    fontSize: Typography.sizes.sm,
    fontWeight: '600',
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
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
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
    fontWeight: '600',
  },
  pickerContainer: {
    borderRadius: Radius.md,
    borderWidth: 1,
    overflow: 'hidden',
    justifyContent: 'center',
  },
  errorText: {
    fontSize: Typography.sizes.sm,
    marginTop: Spacing.lg,
    textAlign: 'center',
    lineHeight: 20,
  },
});
