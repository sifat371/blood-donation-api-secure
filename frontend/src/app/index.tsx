/**
 * Entry point — redirects to auth or main based on auth state.
 */

import { useAuthStore } from "@/store/auth-store";
import { Redirect } from "expo-router";

export default function Index() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isProfileComplete = useAuthStore((s) => s.isProfileComplete);
  const user = useAuthStore((s) => s.user);

  if (!isAuthenticated) {
    return <Redirect href="/(auth)/login" />;
  }

  // `user &&` matters: isProfileComplete is derived from the loaded profile, so
  // it reads false both for an unfinished profile and for one startup couldn't
  // fetch. Sending a registered user to the signup form because the backend was
  // briefly unreachable would be worse than letting them into the app, where the
  // individual screens surface their own errors.
  if (user && !isProfileComplete) {
    return <Redirect href="/(auth)/complete-profile" />;
  }

  return <Redirect href="/(main)" />;
}
