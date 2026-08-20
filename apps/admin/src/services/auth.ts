import { restPath } from "@agentcore/contract-rest-types/paths";
import { api, clearCsrfToken } from "@/services/api";
import type { AuthUser } from "@/stores/auth";
import type { components } from "@/types/api.generated";

type BackendUser = components["schemas"]["UserResponse"];
type LoginResponse = components["schemas"]["LoginResponse"];
type MfaStatusResponse = components["schemas"]["MfaStatusResponse"];
type MfaSetupResponse = components["schemas"]["MfaSetupResponse"];
type MfaConfirmResponse = components["schemas"]["MfaConfirmResponse"];

function toUser(u: BackendUser): AuthUser {
  return {
    id: u.id,
    username: u.username,
    displayName: u.display_name,
    email: u.email,
    emailVerifiedAt: u.email_verified_at ?? null,
    role: u.role,
    passwordMustChange: u.password_must_change,
  };
}

export type LoginOutcome =
  | { kind: "success"; user: AuthUser }
  | { kind: "mfa_required"; pendingToken: string }
  | { kind: "mfa_setup_required"; user: AuthUser };

function parseLoginResponse(res: LoginResponse): LoginOutcome {
  if (res.mfa_required && res.pending_token) {
    return { kind: "mfa_required", pendingToken: res.pending_token };
  }
  if (res.mfa_setup_required && res.user) {
    return { kind: "mfa_setup_required", user: toUser(res.user) };
  }
  if (res.user) {
    return { kind: "success", user: toUser(res.user) };
  }
  throw new Error("登录响应无效");
}

/** Resolve the current session from the access cookie (throws 401 if absent). */
export async function fetchMe(): Promise<AuthUser> {
  return toUser(await api.get<BackendUser>(restPath("/v1/auth/me")));
}

export async function login(
  username: string,
  password: string,
  persistSession = false,
): Promise<LoginOutcome> {
  return parseLoginResponse(
    await api.post<LoginResponse>("/v1/auth/login", {
      username,
      password,
      persist_session: persistSession,
    }),
  );
}

/**
 * Second login factor. The backend branches on which field is present (an authenticator
 * code, or one single-use recovery code), so the caller must pick exactly one —
 * sending both, or neither, is a 422.
 */
export type MfaChallenge = { code: string } | { recoveryCode: string };

export async function loginMfa(
  pendingToken: string,
  challenge: MfaChallenge,
): Promise<LoginOutcome> {
  const field =
    "code" in challenge
      ? { code: challenge.code }
      : { recovery_code: challenge.recoveryCode };
  return parseLoginResponse(
    await api.post<LoginResponse>("/v1/auth/login/mfa", {
      pending_token: pendingToken,
      ...field,
    }),
  );
}

export async function mfaStatus(): Promise<MfaStatusResponse> {
  return api.get<MfaStatusResponse>("/v1/auth/mfa/status");
}

export async function mfaSetup(): Promise<MfaSetupResponse> {
  return api.post<MfaSetupResponse>("/v1/auth/mfa/setup");
}

export async function mfaConfirm(code: string): Promise<MfaConfirmResponse> {
  return api.post<MfaConfirmResponse>("/v1/auth/mfa/confirm", { code });
}

export async function logout(): Promise<void> {
  // The token is purely local state, so dropping it must not hinge on the server
  // call succeeding: callers log out locally either way (`.finally(setUnauthenticated)`),
  // and a token kept past a failed logout is exactly what 403s the *next* session's
  // mutating requests — including its logout, which strands the operator signed in.
  try {
    await api.post("/v1/auth/logout");
  } finally {
    clearCsrfToken();
  }
}

/** Change the signed-in user's password; this session stays logged in. */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await api.post("/v1/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

export interface ProfileUpdate {
  displayName?: string;
  email?: string | null;
}

/** Update profile; returns refreshed user for the auth store. */
export async function updateProfile(update: ProfileUpdate): Promise<AuthUser> {
  const body: Record<string, unknown> = {};
  if (update.displayName !== undefined) body.display_name = update.displayName;
  if (update.email !== undefined) body.email = update.email;
  return toUser(await api.patch<BackendUser>("/v1/auth/me", body));
}
