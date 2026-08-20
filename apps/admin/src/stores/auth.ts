import { create } from "zustand";

export interface AuthUser {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  /** ISO timestamp when the email was verified; null = unverified. */
  emailVerifiedAt: string | null;
  role: string;
  passwordMustChange: boolean;
}

/**
 * - `loading`: bootstrap window before the first `/auth/me` probe resolves.
 * - `forbidden`: a valid session whose account is **not** an admin — the console
 *   is admin-only, so we show a "需要管理员权限" wall instead of the dashboard.
 * - `unavailable`: the backend is unreachable (transport failure).
 */
export type AuthStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "forbidden"
  | "unavailable";

interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
  /** Admin logged in but TOTP not enrolled yet — show MFA setup wizard. */
  mfaSetupRequired: boolean;
  /** Pending MFA challenge during the two-step login flow. */
  pendingMfaToken: string | null;
  setLoading: () => void;
  setAuthenticated: (user: AuthUser, opts?: { mfaSetupRequired?: boolean }) => void;
  setUnauthenticated: () => void;
  setForbidden: (user: AuthUser) => void;
  setUnavailable: () => void;
  setMfaSetupRequired: (required: boolean) => void;
  setPendingMfaToken: (token: string | null) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  status: "loading",
  user: null,
  mfaSetupRequired: false,
  pendingMfaToken: null,
  setLoading: () => set({ status: "loading" }),
  setAuthenticated: (user, opts) =>
    set({
      status: "authenticated",
      user,
      mfaSetupRequired: opts?.mfaSetupRequired ?? false,
      pendingMfaToken: null,
    }),
  setUnauthenticated: () =>
    set({
      status: "unauthenticated",
      user: null,
      mfaSetupRequired: false,
      pendingMfaToken: null,
    }),
  setForbidden: (user) =>
    set({
      status: "forbidden",
      user,
      mfaSetupRequired: false,
      pendingMfaToken: null,
    }),
  setUnavailable: () =>
    set({
      status: "unavailable",
      user: null,
      mfaSetupRequired: false,
      pendingMfaToken: null,
    }),
  setMfaSetupRequired: (required) => set({ mfaSetupRequired: required }),
  setPendingMfaToken: (token) => set({ pendingMfaToken: token }),
}));
