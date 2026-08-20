import { normalizeEmailCodeExpiresIn } from "@/lib/emailAuth";
import { logEvent } from "@/lib/log";
import { clearSidecarAccountAuth } from "@/services/accountToken";
import { clearAgentTownSession } from "@/services/agentTownSession";
import {
  ApiError,
  BASE_URL,
  NetworkError,
  api,
  bootstrapRequest,
  captureCsrf,
  clearCsrfToken,
  fetchWithTimeout,
  getCsrfHeaders,
  isReplayableCsrfRejection,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import { clearSidecarFoldersAuth } from "@/services/foldersToken";
import { clearSidecarInference } from "@/services/inferenceToken";
import { clearDefaultPermissionAxesCache } from "@/services/permissionAxes";
import type { AuthUser } from "@/stores/auth";
import type { components } from "@/types/api.generated";
import { restPath } from "@agentcore/contract-rest-types/paths";

/** Server user payload (`/auth/me|register`), generated from OpenAPI. */
type BackendUser = components["schemas"]["UserResponse"];
type LoginResponse = components["schemas"]["LoginResponse"];
type SessionListResponse = components["schemas"]["SessionListResponse"];
type SessionSummary = components["schemas"]["SessionSummary"];
type StatusResponse = components["schemas"]["StatusResponse"];
type EmailCodeAccepted = components["schemas"]["EmailCodeAcceptedResponse"];
type EmailCodeBody = components["schemas"]["EmailCodeRequest"];
type PasswordForgotBody = components["schemas"]["PasswordForgotRequest"];
type PasswordResetBody = components["schemas"]["PasswordResetRequest"];
type EmailSendCodeBody = components["schemas"]["EmailSendCodeRequest"];

/** One active login device (refresh-token family). Re-exported for UI consumers. */
export type { SessionSummary };

/** Resolve the backend's relative avatar URL (`/v1/users/<id>/avatar?v=…`) against
 *  the API base so consumers can drop it straight into an `<img src>`. Leaves
 *  absolute URLs untouched; null stays null (UI falls back to the initial). */
function avatarSrc(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("/") ? `${BASE_URL}${url}` : url;
}

function toUser(u: BackendUser): AuthUser {
  return {
    id: u.id,
    username: u.username,
    displayName: u.display_name,
    email: u.email,
    emailVerifiedAt: u.email_verified_at ?? null,
    role: u.role,
    avatarUrl: avatarSrc(u.avatar_url),
  };
}

/** Resolve the current session from the access cookie (throws 401 if absent). */
/** Resolve the current session during cold-start bootstrap (bounded wait). */
async function bootstrapFetchMe(): Promise<AuthUser> {
  return toUser(await bootstrapRequest<BackendUser>("/v1/auth/me"));
}

export async function fetchMe(): Promise<AuthUser> {
  return toUser(await api.get<BackendUser>("/v1/auth/me"));
}

export async function login(
  username: string,
  password: string,
): Promise<AuthUser> {
  const body = await api.post<LoginResponse>("/v1/auth/login", {
    username,
    password,
  });
  if (!body.user) {
    throw new Error("登录响应缺少用户信息");
  }
  const user = toUser(body.user);
  // Fresh session → drop any inference token cached for a previous user, so the
  // sidecar never mints under one user then bills another (token is user-scoped).
  clearSidecarInference();
  clearSidecarFoldersAuth();
  clearSidecarAccountAuth();
  clearDefaultPermissionAxesCache(); // 自主度同为按用户的设置，换人重取
  return user;
}

export interface RegisterSendCodeInput {
  email: string;
  password: string;
}

/** Step 1 of two-step register: persist pending signup and email a 6-digit code. */
export async function sendRegisterCode(
  input: RegisterSendCodeInput,
): Promise<{ expiresIn: number }> {
  // Body is email + password only (username is allocated server-side).
  // OpenAPI types may still list the old fields until the next gen:types.
  const body = await api.post<EmailCodeAccepted>(
    restPath("/v1/auth/register/send-code"),
    { email: input.email, password: input.password },
  );
  return { expiresIn: normalizeEmailCodeExpiresIn(body?.expires_in) };
}

/** Step 2 of two-step register: 201 UserResponse, no session cookie. */
export async function verifyRegister(
  email: string,
  code: string,
  displayName?: string,
): Promise<AuthUser> {
  const body: Record<string, string> = { email, code };
  const trimmedName = displayName?.trim();
  if (trimmedName) body.display_name = trimmedName;
  const user = toUser(
    await api.post<BackendUser>(restPath("/v1/auth/register/verify"), body),
  );
  clearSidecarInference(); // fresh session → drop any prior-user token (see login)
  clearSidecarFoldersAuth();
  clearSidecarAccountAuth();
  clearDefaultPermissionAxesCache();
  return user;
}

/** Always 202 — does not reveal whether the email is registered. */
export async function forgotPassword(email: string): Promise<void> {
  const body: PasswordForgotBody = { email };
  await api.post<EmailCodeAccepted>(restPath("/v1/auth/password/forgot"), body);
}

export async function resetPassword(
  email: string,
  code: string,
  newPassword: string,
): Promise<void> {
  const body: PasswordResetBody = {
    email,
    code,
    new_password: newPassword,
  };
  await api.post<StatusResponse>(restPath("/v1/auth/password/reset"), body);
}

/** Logged-in catch-up: send a verification code to `email`. */
export async function sendEmailCode(email: string): Promise<void> {
  const body: EmailSendCodeBody = { email };
  await api.post<EmailCodeAccepted>(restPath("/v1/auth/email/send-code"), body);
}

/** Logged-in catch-up: confirm the 6-digit code and refresh the user. */
export async function verifyEmail(
  email: string,
  code: string,
): Promise<AuthUser> {
  const body: EmailCodeBody = { email, code };
  return toUser(
    await api.post<BackendUser>(restPath("/v1/auth/email/verify"), body),
  );
}

/**
 * End the session: ask the server to revoke it, then drop every credential this
 * process still holds.
 *
 * The local wipe is deliberately NOT conditional on the server call. It used to
 * sit behind a bare `await`, so a refused logout (403 CSRF was the biggest single
 * cluster in production logs) skipped all of it and left the app holding the
 * previous user's CSRF token, sidecar credentials, permission cache and
 * AgentTown session — while the UI still dropped to the login screen, so the
 * next account silently inherited them. Server-side revocation is best-effort
 * from here; the local state is the part this process actually owns.
 */
export async function logout(): Promise<void> {
  try {
    await api.post("/v1/auth/logout");
  } catch (err) {
    // Never rethrown: the caller's job is to drop to login either way. Logged so
    // "session may still be live server-side" stays visible in desktop.jsonl.
    logEvent("warn", "auth.logout", {
      result: "server_revoke_failed",
      reason: err instanceof NetworkError ? "network" : "http",
      status: err instanceof ApiError ? err.status : undefined,
      code: err instanceof ApiError ? err.code : undefined,
    });
  } finally {
    clearCsrfToken();
    clearSidecarInference(); // session ended → next login re-mints
    clearSidecarFoldersAuth();
    clearSidecarAccountAuth();
    clearDefaultPermissionAxesCache();
    void clearAgentTownSession();
  }
}

/** List the caller's active login devices (one row per refresh-token family). */
export async function listSessions(): Promise<SessionListResponse> {
  return api.get<SessionListResponse>("/v1/auth/sessions");
}

/** Log out one device by refresh-token family id. Revoking the current family
 *  ends this session — callers should then run the normal logout clear-state path. */
export async function revokeSession(familyId: string): Promise<void> {
  await api.delete<StatusResponse>(
    `/v1/auth/sessions/${encodeURIComponent(familyId)}`,
  );
}

/** Log out every other device; keep the caller's current family. */
export async function revokeOtherSessions(): Promise<void> {
  await api.post<StatusResponse>("/v1/auth/sessions/revoke-others");
}

/**
 * Change the signed-in user's password (修改密码). The backend revokes every other
 * device's session and re-issues this one's cookies, so the caller stays logged in
 * — no re-login needed here. Throws {@link ApiError} (401 wrong current password,
 * 422 weak/unchanged new password) for the form to surface.
 */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await api.post("/v1/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

/** Profile fields the user may edit. Omit a key to leave it unchanged; pass
 *  `email: null` to clear it (PATCH semantics, mirrored on the backend). */
export interface ProfileUpdate {
  displayName?: string;
  username?: string;
  email?: string | null;
}

/** Update the signed-in user's profile (个人资料编辑); returns the refreshed user so
 *  the caller can sync the auth store. 422 if the email is already taken. */
export async function updateProfile(update: ProfileUpdate): Promise<AuthUser> {
  const body: Record<string, unknown> = {};
  if (update.displayName !== undefined) body.display_name = update.displayName;
  if (update.username !== undefined) body.username = update.username;
  if (update.email !== undefined) body.email = update.email;
  return toUser(await api.patch<BackendUser>("/v1/auth/me", body));
}

/**
 * Upload a new avatar (头像上传). The backend reads the **raw image bytes** (no
 * multipart) and re-encodes them to a square WebP, so we POST the File directly —
 * the shared `api` helper can't be used as it JSON-encodes the body. Returns the
 * refreshed user (its `avatarUrl` carries a content-hash cache-buster, so the new
 * picture shows immediately). Mirrors `api.ts`'s recovery policy — refresh-once
 * on 401 and the same one-shot CSRF-403 replay, so the one raw-bytes write that
 * bypasses `api.*` is not the single button that hard-fails on a stale token.
 */
export async function uploadAvatar(file: File): Promise<AuthUser> {
  const send = async (): Promise<Response> => {
    const res = await fetch(`${BASE_URL}/v1/users/me/avatar`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        // A mutating cookie-auth request: the CSRF header is not optional just
        // because the body is raw bytes instead of JSON (backend 403s otherwise).
        ...getCsrfHeaders("POST"),
      },
      body: file,
    });
    captureCsrf(res);
    return res;
  };
  let res: Response;
  // One replay for the whole call, shared by both recoveries — the same bound as
  // `request`'s `retry` flag, so a server that keeps rejecting costs one extra
  // attempt, never a loop.
  let replayed = false;
  try {
    res = await send();
    if (res.status === 401) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        res = await send();
        replayed = true;
        if (res.status === 401) {
          notifyUnauthorized({ reason: "replay_still_401", via: "avatar" });
        }
      } else if (outcome === "auth_dead") {
        notifyUnauthorized({ reason: "refresh_auth_dead", via: "avatar" });
      }
      // `transient` falls through to the ApiError below — a flaky refresh must
      // never read as session death.
    }
    // Safe to resend: the verdict only holds when the rejection came out of
    // middleware before the handler ran, so the server never stored a picture.
    if (!replayed && !res.ok) {
      const rejected = new ApiError(
        res.status,
        await res.clone().text(),
        res.headers,
      );
      if (isReplayableCsrfRejection(res, rejected)) res = await send();
    }
  } catch (cause) {
    throw new NetworkError(cause);
  }
  if (!res.ok) throw new ApiError(res.status, await res.text(), res.headers);
  return toUser((await res.json()) as BackendUser);
}

/** Remove the avatar and fall back to the initial (恢复默认头像). Idempotent on the
 *  backend; returns the refreshed user with `avatarUrl: null`. */
export async function deleteAvatar(): Promise<AuthUser> {
  return toUser(await api.delete<BackendUser>("/v1/users/me/avatar"));
}

/**
 * Self-service account deletion (注销账户). The password re-confirms intent; the
 * backend soft-deletes + anonymizes the account and revokes all sessions. The
 * caller must drop to the login screen afterwards. Throws {@link ApiError} (401
 * wrong password) for the form to surface.
 */
export async function deleteAccount(password: string): Promise<void> {
  await api.delete("/v1/auth/me", { password });
  clearSidecarInference(); // account gone → drop any cached inference token
  clearSidecarFoldersAuth();
  clearSidecarAccountAuth();
  clearDefaultPermissionAxesCache();
}

/**
 * A transport failure or a 5xx — i.e. the backend is down or broken — as opposed
 * to a 401 that merely means "not logged in". This split is what lets the app
 * show a retry screen instead of a login form during an outage.
 */
function isOutage(err: unknown): boolean {
  return (
    err instanceof NetworkError ||
    (err instanceof ApiError && err.status >= 500)
  );
}

// Hand-written on purpose: `/readyz` has no response_model, so the generated
// type is an untyped dict — this local shape stays the precise contract.
interface ReadinessResponse {
  status: "ready" | "not_ready";
  database: boolean;
}

/**
 * Probe backend readiness via `/readyz`. Returns null when everything is
 * reachable, or a user-facing reason when it isn't. Uses raw fetch so the 503
 * body (which `api.get` would raise as an ApiError) stays readable.
 *
 * Exported so the gate can reuse the exact same diagnosis to confirm a
 * mid-session outage before taking over the screen.
 */
export async function diagnoseOutage(): Promise<string | null> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/readyz`, {
      credentials: "include",
    });
    const ready = (await res.json()) as ReadinessResponse;
    if (res.ok && ready.database) return null;
    // Mass-user copy: no「请确认数据库已启动 / 请起后端」. Dev sees real cause in logs.
    return "AgentCore 服务暂时不可用，请稍后重试。";
  } catch {
    return "连不上 AgentCore 服务，请稍后重试。";
  }
}

type DevLoginResult =
  | { kind: "ok"; user: AuthUser }
  | { kind: "skipped" }
  | { kind: "unavailable" }
  | { kind: "failed" };

/**
 * Dev-only convenience: log in through the real `/auth/login` flow using
 * credentials from `.env.local` (VITE_DEV_USERNAME / VITE_DEV_PASSWORD) so you
 * don't retype them on every restart. No-op in production builds (the
 * `import.meta.env.DEV` guard is statically eliminated) or when vars are unset.
 *
 * This never bypasses the backend auth check — it just automates a normal login
 * with a seeded dev user. Unlike a bare null, the result tells an outage apart
 * from bad credentials and (in dev) logs the real reason instead of swallowing
 * it: a silent catch here is exactly what made a DB outage look like a broken
 * login feature.
 */
async function devAutoLogin(): Promise<DevLoginResult> {
  if (!import.meta.env.DEV) return { kind: "skipped" };
  const username = import.meta.env.VITE_DEV_USERNAME;
  const password = import.meta.env.VITE_DEV_PASSWORD;
  if (!username || !password) return { kind: "skipped" };
  try {
    const body = await bootstrapRequest<LoginResponse>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    if (!body.user) {
      throw new Error("登录响应缺少用户信息");
    }
    const user = toUser(body.user);
    clearSidecarInference();
    clearSidecarFoldersAuth();
    clearSidecarAccountAuth();
    clearDefaultPermissionAxesCache();
    return { kind: "ok", user };
  } catch (err) {
    if (isOutage(err)) {
      console.warn("[dev] auto-login skipped: backend unavailable", err);
      return { kind: "unavailable" };
    }
    console.warn("[dev] auto-login failed: check VITE_DEV_* credentials", err);
    return { kind: "failed" };
  }
}

export type BootstrapResult =
  | { kind: "authenticated"; user: AuthUser }
  | { kind: "unauthenticated" }
  | { kind: "unavailable"; reason: string };

/**
 * One structured line per cold-start outcome, landing in the desktop product log
 * (`userData/logs/desktop.jsonl`) tagged prod/dev by the main process. This is
 * what makes the "have to log in every launch" class of bug observable AND tells
 * an installed build apart from a dev build: only dev ever logs `dev_auto_login`
 * (the .env.local path that silently masked the missing-refresh bug).
 */
type BootstrapOutcome =
  | "me_ok"
  | "refreshed"
  | "dev_auto_login"
  | "logged_out"
  | "outage";

function logBootstrap(
  outcome: BootstrapOutcome,
  fields?: Record<string, unknown>,
): void {
  logEvent(outcome === "outage" ? "warn" : "info", "auth.bootstrap", {
    result: outcome,
    ...fields,
  });
}

/**
 * Resolve the initial auth state on app start. Critically, it tells an
 * infrastructure outage apart from "not logged in" so the gate can show a retry
 * screen rather than a login form the user could never get past.
 */
export async function bootstrapAuth(): Promise<BootstrapResult> {
  // 1. Existing session via the access cookie.
  try {
    const user = await bootstrapFetchMe();
    logBootstrap("me_ok");
    return { kind: "authenticated", user };
  } catch (err) {
    if (isOutage(err)) {
      const reason =
        (await diagnoseOutage()) ?? "AgentCore 服务暂时不可用，请稍后重试。";
      logBootstrap("outage", { stage: "me", reason });
      return { kind: "unavailable", reason };
    }
    // 401 → the access token is absent/expired. This does NOT mean the session
    // is gone: the refresh cookie outlives the access cookie by days. Fall
    // through to a silent refresh before concluding the user is logged out.
  }

  // 2. Silent refresh on cold start. The access cookie's TTL (~30min) is far
  //    shorter than the refresh cookie's (~30d), so relaunching the app any real
  //    time later finds /auth/me 401'ing on an expired access token while the
  //    refresh token is still perfectly valid. `request()` deliberately skips
  //    its own 401→refresh for /v1/auth/* paths (so login/refresh never
  //    recurse), which means /auth/me can't self-heal — bootstrap must drive the
  //    refresh here, or every relaunch past the access TTL forces a needless
  //    re-login (precisely the "have to log in every time I open the app" bug).
  try {
    const outcome = await tryRefresh();
    if (outcome === "renewed") {
      const user = await bootstrapFetchMe();
      logBootstrap("refreshed");
      return { kind: "authenticated", user };
    }
    if (outcome === "transient") {
      const reason =
        (await diagnoseOutage()) ?? "AgentCore 服务暂时不可用，请稍后重试。";
      logBootstrap("outage", { stage: "refresh", reason });
      return { kind: "unavailable", reason };
    }
  } catch (err) {
    if (isOutage(err)) {
      const reason =
        (await diagnoseOutage()) ?? "AgentCore 服务暂时不可用，请稍后重试。";
      logBootstrap("outage", { stage: "refresh", reason });
      return { kind: "unavailable", reason };
    }
    // Refreshed but /auth/me still 401'd → genuinely logged out; fall through.
  }

  // 3. Dev convenience auto-login (no-op in prod / when unconfigured).
  const dev = await devAutoLogin();
  if (dev.kind === "ok") {
    logBootstrap("dev_auto_login");
    return { kind: "authenticated", user: dev.user };
  }

  // 4. No session. If the backend is actually unreachable, surface that instead
  //    of a doomed login form; otherwise it's a genuine logged-out state.
  const reason = await diagnoseOutage();
  if (reason) {
    logBootstrap("outage", { stage: "final", reason });
    return { kind: "unavailable", reason };
  }
  logBootstrap("logged_out");
  return { kind: "unauthenticated" };
}
