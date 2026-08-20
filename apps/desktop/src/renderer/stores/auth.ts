import { create } from "zustand";

export interface AuthUser {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  /** ISO timestamp when the email was verified; null = unverified. */
  emailVerifiedAt: string | null;
  role: string;
  /** Ready-to-render avatar URL (头像), already absolute (services/auth resolves the
   * backend's relative path against the API base); null = no avatar, show the initial. */
  avatarUrl: string | null;
}

/**
 * - `loading`: bootstrap window before the first probe resolves; the UI shows a
 *   splash so we never flash the login screen at an already-authed user.
 * - `unavailable`: the backend itself is unreachable (e.g. the database is
 *   down). Distinct from `unauthenticated` so we show a retry screen instead of
 *   a login form that's guaranteed to fail.
 */
export type AuthStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "unavailable";

interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
  /**
   * Whether the server has acknowledged this session in *this* process — i.e.
   * the handshake actually completed. An offline read-only shell is
   * `authenticated` off a cached identity the server has never seen, so
   * everything the handshake hands out (the CSRF token above all) is missing
   * until the session is reconciled; the AuthGate owns that reconciliation.
   */
  sessionVerified: boolean;
  /** User-facing outage reason; set only while status === "unavailable". */
  reason: string | null;
  setLoading: () => void;
  setAuthenticated: (user: AuthUser) => void;
  /** Enter the shell from cache while the backend is unreachable (N4-A 只读离线). */
  setOfflineSession: (user: AuthUser) => void;
  setUnauthenticated: () => void;
  setUnavailable: (reason: string) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  status: "loading",
  user: null,
  sessionVerified: false,
  reason: null,
  setLoading: () => set({ status: "loading", reason: null }),
  setAuthenticated: (user) =>
    set({ status: "authenticated", user, reason: null, sessionVerified: true }),
  setOfflineSession: (user) =>
    set({
      status: "authenticated",
      user,
      reason: null,
      sessionVerified: false,
    }),
  setUnauthenticated: () =>
    set({
      status: "unauthenticated",
      user: null,
      reason: null,
      sessionVerified: false,
    }),
  setUnavailable: (reason) =>
    set({ status: "unavailable", user: null, reason, sessionVerified: false }),
}));
