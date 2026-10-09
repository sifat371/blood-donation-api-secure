import { create } from "zustand";
import { PermissionStatus } from "./location-store";

export interface NotificationState {
  pushToken: string | null;
  permissionGranted: PermissionStatus;
  setPermissionGranted: (status: PermissionStatus) => void;
  setPushToken: (token: string) => void;
}

export const useNotificationStore = create<NotificationState>((set) => ({
  pushToken: null,
  permissionGranted: "undetermined",
  setPermissionGranted: (granted: PermissionStatus) =>
    set({ permissionGranted: granted }),
  setPushToken: (token: string) => set({ pushToken: token }),
}));
