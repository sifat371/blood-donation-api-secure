import { create } from "zustand";

export type PermissionStatus = "undetermined" | "granted" | "denied";

export interface LocationState {
  permissionGranted: PermissionStatus;
  latitude: number | null;
  longitude: number | null;

  setPermissionGranted: (status: PermissionStatus) => void;
  /**
   * Order is (latitude, longitude) — matching `coords` from expo-location and
   * the backend's field order everywhere.
   *
   * This used to be declared as (latitude, longitude) but implemented as
   * (longitude, latitude). Both are `number`, so TypeScript accepted every
   * call site while silently transposing the values: a Dhaka user at
   * 23.75N/90.37E was stored as 90.37N/23.75E, which is in the Arctic Ocean.
   * Donor search and nearby-request distances were computed from that.
   */
  setLocation: (latitude: number, longitude: number) => void;
}

export const useLocationStore = create<LocationState>((set) => ({
  permissionGranted: "undetermined",
  latitude: null,
  longitude: null,

  setPermissionGranted: (granted: PermissionStatus) =>
    set({ permissionGranted: granted }),

  setLocation: (latitude: number, longitude: number) =>
    set({
      latitude,
      longitude,
    }),
}));
