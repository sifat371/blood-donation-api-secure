import { z } from 'zod';

export const BloodGroupSchema = z.enum(['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']);

export const UserProfileSchema = z.object({
  id: z.number(),
  name: z.string(),
  email: z.string().email(),
  phone: z.string().optional(),
  blood_group: BloodGroupSchema.optional(),
  division: z.string().optional(),
  district: z.string().optional(),
  upazila: z.string().optional(),
  latitude: z.number().optional(),
  longitude: z.number().optional(),
  last_donation_date: z.string().optional(),
  is_available: z.boolean(),
  gender: z.string().optional(),
});

export const BloodRequestSchema = z.object({
  id: z.number(),
  patient_name: z.string().nullable(),
  blood_group: BloodGroupSchema,
  units: z.number().min(1),
  hospital_name: z.string(),
  status: z.enum(['Pending', 'Partially Committed', 'Fully Committed', 'Completed', 'Cancelled', 'Expired']),
  needed_date: z.string(),
  contact_number: z.string().nullable(),
});
