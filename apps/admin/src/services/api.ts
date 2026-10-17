// HTTP client for the admin console. In production the console is served from its
// own domain alongside its own `/api` reverse proxy, so requests are SAME-origin and
// the session cookie is first-party. That is deliberate, not incidental: the auth
// cookie carries no `domain=` and lands on whichever API host is called, so pointing
// the console at the product API would hand both SPAs one shared access cookie and
// let the later login silently take over the earlier one (deploy/nginx/office-admin.conf).
// Local dev is still cross-origin (:5174 → :8000, same-site localhost), so requests
// are credentialed and a CORS allowlist entry is kept either way — see README.
// Mirrors the desktop renderer's api.ts: typed ApiError over the backend's
// `{error:{code,message}}` contract, a NetworkError for transport failures, and a
// single replay of the failed request — after a refresh on 401, and straight away
// on the CSRF 403 that hands back a usable token.

import { clientHeaders } from "@/lib/clientBuildInfo";

export const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  /** Backend error code from the `{error:{code,message}}` contract, when present. */
  readonly code?: string;
  /** Backend user-facing message (often a ready-to-show zh string). */
  readonly serverMessage?: string;
  /** ISO-8601 UTC instant a lockout (or other cooldown) ends. The sentence stays
   * free of a clock time; the login form counts down from this. */
  readonly recoveryAt?: string;

  constructor(
    public status: number,
    public body: string,
  ) {
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
    try {
      const parsed = JSON.parse(body) as {
        error?: { code?: string; message?: string; recovery_at?: string };
      };
      this.code = parsed.error?.code;
      this.serverMessage = parsed.error?.message;
      const at = parsed.error?.recovery_at;
      if (typeof at === "string" && at) this.recoveryAt = at;
    } catch {
      /* non-JSON body — keep the raw text only */
    }
  }
}

/** The request never completed at the transport layer (server down / CORS). */
export class NetworkError extends Error {
  constructor(public readonly detail?: unknown) {
    super("network request failed");
    this.name = "NetworkError";
  }
}

// Invoked when a request stays 401 even after a refresh, so the app drops to the
// login screen. Registered by the auth bootstrap to avoid a store import cycle.
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// SameSite is not CSRF protection, so mutating requests must echo a token the
// backend minted. It arrives on the `X-CSRF-Token` header of the handshake
// responses (login / refresh, plus the cold-start `/me` — the only handshake a
// still-live access cookie gets) and of the very 403 that rejects a request for
// lacking one, so both fetch sites below capture it unconditionally, before
// branching on ok/error — that re-arm is what makes the 403 recoverable by
// retrying the same request.
let csrfToken: string | null = null;

/**
 * Take the token off any response carrying one, and report whether this response
 * carried it. On a rejection that answer is the backend's own verdict on whether
 * the session just re-armed, which is what {@link request} replays on.
 */
function captureCsrf(response: Response): boolean {
  const token = response.headers.get("X-CSRF-Token");
  if (!token) return false;
  csrfToken = token;
  return true;
}

export function clearCsrfToken(): void {
  csrfToken = null;
}

function csrfHeaders(method: string): Record<string, string> {
  if (!csrfToken) return {};
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return {};
  return { "X-CSRF-Token": csrfToken };
}

// The credential/session endpoints whose 401 is *expected* (bad password, no
// session yet) — they must NOT trigger the refresh-replay-then-logout flow.
const isAuthPath = (path: string): boolean => path.startsWith("/v1/auth/");

// Single in-flight refresh shared by every concurrent 401 caller (desktop
// tryRefresh parity). Refresh tokens rotate on first use — racing multiple
// POSTs would trip reuse detection and revoke the family. Backend grace is the
// cross-tab backstop; we only collapse same-process bursts here.
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Silent cookie refresh. Exported for cold-start bootstrap: `/v1/auth/me` is an
 * auth path so {@link request} will not auto-refresh on its 401 — bootstrap must
 * call this explicitly, then retry `/me`. Regular API 401s still go through the
 * in-request refresh below (non-auth paths only, to avoid refresh recursion).
 *
 * Single-flight: concurrent callers share one `/refresh` round-trip.
 */
export function tryRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async (): Promise<boolean> => {
    try {
      const res = await fetch(`${BASE_URL}/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      captureCsrf(res);
      return res.ok;
    } catch {
      return false;
    }
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  retry = false,
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  // `...options` must come *before* `headers`: spread after, a caller passing any
  // headers at all would replace the whole merged object and silently drop the
  // CSRF token and Content-Type with it.
  const fetchInit: RequestInit = {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
      ...csrfHeaders(method),
      ...options.headers,
    },
  };
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, fetchInit);
  } catch (cause) {
    throw new NetworkError(cause);
  }

  const rearmed = captureCsrf(response);

  if (response.ok) {
    // 204/empty bodies: don't choke on an absent JSON payload.
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  if (response.status === 401 && !isAuthPath(path)) {
    if (!retry && (await tryRefresh())) {
      return request<T>(path, options, true);
    }
    onUnauthorized?.();
  }

  const error = new ApiError(response.status, await response.text());

  // A CSRF 403 that hands a token back is the backend certifying this session
  // re-armed itself, so the same request replays cleanly — 401 parity, sharing the
  // one `retry` flag so a rejection that survives the replay stops there. The
  // backend withholds the token precisely when replaying would be wrong (the token
  // was signed for another session, and the write would land on whoever owns the
  // cookie now); that 403 keeps surfacing to the operator.
  if (
    response.status === 403 &&
    error.code === "CSRF_FAILED" &&
    rearmed &&
    !retry
  ) {
    return request<T>(path, options, true);
  }

  throw error;
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>(path, init ?? {}),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/** A user-facing zh message for any thrown api error (backend msg → status → net). */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    // The one backend message that is English ("CSRF token missing or invalid.
    // Re-login and retry.") and whose fix is a *client* action, so it is phrased
    // here rather than passed through. The rejections that re-seed the token are
    // already replayed above, so what still reaches the operator is the session
    // that cannot re-arm — looping on that behind their back would dress a dead
    // session up as an action that worked. Repeating it by hand stays cheap, and
    // neither a reload nor a re-login is the missing step.
    if (err.code === "CSRF_FAILED") {
      return "安全校验未通过，请重试";
    }
    if (err.serverMessage) return err.serverMessage;
    if (err.status === 403) return "需要管理员权限";
    if (err.status === 401) return "登录已失效，请重新登录";
    return `请求失败（${err.status}）`;
  }
  if (err instanceof NetworkError) return "无法连接后端，请确认服务已启动";
  return "发生未知错误";
}

/**
 * The backend's own message, else a form-specific `fallback` ("保存失败，请重试") —
 * for pages that want their own phrasing instead of the generic status wording.
 *
 * Errors {@link errorMessage} deliberately re-phrases (CSRF) still win: a page that
 * reached for `serverMessage` directly would put the backend's English string on
 * screen, which is how the console used to show "CSRF token missing or invalid."
 */
export function errorMessageOr(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.code === "CSRF_FAILED") return errorMessage(err);
  return err instanceof ApiError ? (err.serverMessage ?? fallback) : fallback;
}
