// Auth flow for the mobile bearer client (M3). Register is two-step
// (/register/send-code + /verify); session uses bearer /v1/auth/token*.
// REST DTOs track OpenAPI via @agentcore/contract-rest-types.
import {
  apiFetch,
  apiUrl,
  clearTokens,
  getTokens,
  hydrateTokens,
  setTokens,
} from "@/api/client";
import { startFulfill, stopFulfill } from "@/api/fulfill";
import { disablePush, enablePush } from "@/api/push";
import { startRealtime, stopRealtime } from "@/api/realtime";
import { clearAiAttention } from "@/lib/aiAttention";
import { clearAiTurnActivity } from "@/lib/aiTurnActivity";
import { clientHeaders } from "@/lib/clientBuildInfo";
import { clearConversationListCache } from "@/lib/conversationListCache";
import { normalizeEmailCodeExpiresIn } from "@/lib/emailAuth";
import type { components } from "@/types/api.generated";
import { restPath } from "@agentcore/contract-rest-types/paths";

type Schemas = components["schemas"];

export type User = Schemas["UserResponse"];

/** Login REST failure: keeps `{error.code}` so the page can branch without matching copy. */
export class AuthApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
    this.name = "AuthApiError";
  }
}

function asUser(raw: unknown): User {
  return raw as User;
}

type TokenResponse = Schemas["TokenResponse"];

// /readyz has no response_model — keep a local shape (mirrors desktop auth.ts).
interface ReadinessResponse {
  status: "ready" | "not_ready";
  database: boolean;
}

export interface RegisterSendCodeInput {
  email: string;
  password: string;
}

/** Step 1 of two-step register: persist pending signup and email a 6-digit code. */
export async function sendRegisterCode(
  input: RegisterSendCodeInput,
): Promise<{ expiresIn: number }> {
  const res = await fetch(apiUrl(restPath("/v1/auth/register/send-code")), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
    },
    // email + password only; username is allocated server-side.
    // OpenAPI types may still list the old fields until the next gen:types.
    body: JSON.stringify({
      email: input.email,
      password: input.password,
    }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "发送验证码失败"));
  }
  let body: Schemas["EmailCodeAcceptedResponse"] | undefined;
  try {
    body = (await res.json()) as Schemas["EmailCodeAcceptedResponse"];
  } catch {
    body = undefined;
  }
  return { expiresIn: normalizeEmailCodeExpiresIn(body?.expires_in) };
}

/** Step 2 of two-step register: same success payload as the old `/register`. */
export async function verifyRegister(
  email: string,
  code: string,
  displayName?: string,
): Promise<User> {
  const body: { email: string; code: string; display_name?: string } = {
    email,
    code,
  };
  const trimmedName = displayName?.trim();
  if (trimmedName) body.display_name = trimmedName;
  const res = await fetch(apiUrl(restPath("/v1/auth/register/verify")), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "注册失败"));
  }
  return asUser(await res.json());
}

/** Always 202 — does not reveal whether the email is registered. */
export async function forgotPassword(email: string): Promise<void> {
  const res = await fetch(apiUrl(restPath("/v1/auth/password/forgot")), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
    },
    body: JSON.stringify({ email } satisfies Schemas["PasswordForgotRequest"]),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "发送验证码失败"));
  }
}

export async function resetPassword(
  email: string,
  code: string,
  newPassword: string,
): Promise<void> {
  const res = await fetch(apiUrl(restPath("/v1/auth/password/reset")), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
    },
    body: JSON.stringify({
      email,
      code,
      new_password: newPassword,
    } satisfies Schemas["PasswordResetRequest"]),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "重置密码失败"));
  }
}

export async function login(username: string, password: string): Promise<User> {
  const res = await fetch(apiUrl("/v1/auth/token"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
    },
    body: JSON.stringify({
      username,
      password,
      persist_session: true,
    } satisfies Schemas["LoginRequest"]),
  });
  if (!res.ok) {
    throw await loginError(res);
  }
  const data = (await res.json()) as TokenResponse;
  setTokens({
    access_token: data.access_token,
    refresh_token: data.refresh_token,
  });
  void enablePush();
  startRealtime();
  startFulfill();
  return data.user ? asUser(data.user) : await me();
}

export async function me(): Promise<User> {
  const res = await apiFetch("/v1/auth/me");
  if (!res.ok) throw new Error("未认证");
  return asUser(await res.json());
}

export async function logout(): Promise<void> {
  const tokens = getTokens();
  if (tokens) {
    await disablePush();
    await fetch(apiUrl("/v1/auth/token/revoke"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...clientHeaders(),
      },
      body: JSON.stringify({
        refresh_token: tokens.refresh_token,
      } satisfies Schemas["TokenRevokeRequest"]),
    }).catch(() => {});
  }
  stopRealtime();
  stopFulfill();
  clearAiAttention();
  clearAiTurnActivity();
  clearConversationListCache();
  clearTokens();
}

export type BootstrapResult =
  | { kind: "authenticated" }
  | { kind: "unauthenticated" }
  | { kind: "unavailable"; reason: string };

/** Probe backend readiness via /readyz. */
export async function diagnoseOutage(): Promise<string | null> {
  try {
    const res = await fetch(apiUrl("/readyz"));
    const ready = (await res.json()) as ReadinessResponse;
    if (res.ok && ready.database) return null;
    return "AgentCore 服务暂时不可用，请稍后重试。";
  } catch {
    return "连不上 AgentCore 服务，请稍后重试。";
  }
}

let bootstrapOnce: Promise<BootstrapResult> | null = null;

export function bootstrapAuth(force = false): Promise<BootstrapResult> {
  if (force) bootstrapOnce = null;
  if (!bootstrapOnce) bootstrapOnce = runBootstrap();
  return bootstrapOnce;
}

async function runBootstrap(): Promise<BootstrapResult> {
  await hydrateTokens();
  if (getTokens()) {
    try {
      const res = await apiFetch("/v1/auth/me");
      if (res.ok) {
        void enablePush();
        startRealtime();
        startFulfill();
        return { kind: "authenticated" };
      }
      if (res.status !== 401) {
        return { kind: "unavailable", reason: await outageReason() };
      }
      clearTokens();
    } catch {
      return { kind: "unavailable", reason: await outageReason() };
    }
  }
  if (await devAutoLogin()) return { kind: "authenticated" };

  const reason = await diagnoseOutage();
  return reason ? { kind: "unavailable", reason } : { kind: "unauthenticated" };
}

async function outageReason(): Promise<string> {
  return (await diagnoseOutage()) ?? "AgentCore 服务暂时不可用，请稍后重试。";
}

async function devAutoLogin(): Promise<boolean> {
  if (!import.meta.env.DEV) return false;
  const username = import.meta.env.VITE_DEV_USERNAME;
  const password = import.meta.env.VITE_DEV_PASSWORD;
  if (!username || !password) return false;
  try {
    await login(username, password);
    return true;
  } catch (err) {
    console.warn("[dev] auto-login failed: check VITE_DEV_* / backend", err);
    return false;
  }
}

async function readErrorBody(
  res: Response,
): Promise<{ code?: string; message?: string }> {
  try {
    const body = (await res.json()) as {
      error?: { code?: string; message?: string };
    };
    return { code: body.error?.code, message: body.error?.message };
  } catch {
    return {};
  }
}

async function errorMessage(res: Response, fallback: string): Promise<string> {
  const { message } = await readErrorBody(res);
  return message ?? `${fallback} (${res.status})`;
}

/** Login-only: 403 EMAIL_NOT_VERIFIED ≠ 401 wrong password. No intercept page. */
async function loginError(res: Response): Promise<AuthApiError> {
  const { code, message } = await readErrorBody(res);
  const text =
    code === "EMAIL_NOT_VERIFIED"
      ? "请先验证邮箱"
      : res.status === 401
        ? "用户名或密码错误"
        : (message ?? `登录失败 (${res.status})`);
  return new AuthApiError(res.status, text, code);
}
