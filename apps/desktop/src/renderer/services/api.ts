import { clientHeaders } from "@/lib/clientBuildInfo";
import { logEvent } from "@/lib/log";
import type { RecoveryMomentFields } from "@/lib/recoveryMoment";
import {
  bearerAuthHeader,
  getBearerTokens,
  isBearerAuth,
  sessionCredentials,
  setBearerTokens,
} from "@/lib/sessionAuth";
import type { AuthRefreshResult } from "../../shared/outbox-contract";

export type { AuthRefreshResult };

export const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Bounded wait for auth-gate probes so a hung backend never strands the UI on "加载中…". */
export const BOOTSTRAP_TIMEOUT_MS = 10_000;

/**
 * `fetch` with an abort deadline. Timeouts surface as {@link NetworkError} so
 * bootstrap/outage paths treat a stuck server like any other transport failure.
 */
export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = BOOTSTRAP_TIMEOUT_MS,
): Promise<Response> {
  try {
    return await fetch(input, {
      ...init,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "TimeoutError") {
      throw new NetworkError(cause);
    }
    if (cause instanceof NetworkError) throw cause;
    throw new NetworkError(cause);
  }
}

export class ApiError extends Error {
  /** Backend error code from the `{error:{code,message}}` contract (main.py's
   * global handler over the AgentCoreError hierarchy), when the body parses. Lets
   * callers branch on the cause without string-matching the message. */
  readonly code?: string;
  /** The backend's user-facing message from the same contract — often a ready-to
   * -show zh string. Distinct from {@link body}, which is the raw (possibly
   * non-JSON) response text kept for logging. */
  readonly serverMessage?: string;
  /** Seconds to wait before retrying, from a `Retry-After` header (e.g. 429s). */
  readonly retryAfter?: number;
  /** 额度恢复 / 配额重置的绝对时刻（ISO8601 UTC）——429 的句子不再自带时刻，由渲染层
   * 按用户本机时区成文（`lib/recoveryMoment`）。 */
  readonly recoveryMoment?: RecoveryMomentFields;

  constructor(
    public status: number,
    public body: string,
    headers?: Headers,
  ) {
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
    // Every backend error is `{error:{code,message}}`; parse it so the shared
    // error map (lib/errors) can phrase REST failures the same way it phrases
    // SSE-turn failures. A non-JSON body (e.g. a proxy error page) just leaves
    // code/serverMessage undefined and callers fall back to status phrasing.
    try {
      const parsed = JSON.parse(body) as {
        error?: { code?: string; message?: string } & RecoveryMomentFields;
        detail?: { code?: string; message?: string } | string;
      };
      this.code = parsed.error?.code;
      this.serverMessage = parsed.error?.message;
      if (parsed.error) {
        this.recoveryMoment = {
          recovery_at: parsed.error.recovery_at,
          reset_at: parsed.error.reset_at,
        };
      }
      // FastAPI HTTPException(detail={code, message}) — P1 interaction 410/409.
      if (!this.code && typeof parsed.detail === "object" && parsed.detail) {
        this.code = parsed.detail.code;
        this.serverMessage = parsed.detail.message ?? this.serverMessage;
      }
    } catch {
      /* non-JSON body — keep the raw text only */
    }
    const ra = Number(headers?.get("Retry-After"));
    this.retryAfter = Number.isFinite(ra) && ra > 0 ? ra : undefined;
  }
}

/**
 * The request never completed at the transport layer (server unreachable, DNS
 * failure, offline, blocked CORS preflight). Distinct from {@link ApiError},
 * which means the server *did* respond, just with a non-2xx status. Callers use
 * this split to tell a backend outage apart from a 401/4xx.
 */
export class NetworkError extends Error {
  constructor(public readonly detail?: unknown) {
    super("network request failed");
    this.name = "NetworkError";
  }
}

// Invoked when a request stays unauthorized even after a refresh attempt, so the
// app can drop to the login screen. Registered by the auth gate to avoid a
// store import cycle (the auth store must not depend on this module).
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** Drop to the login screen via the registered handler (no-op if unset). */
let lastSessionKickAt = 0;

/**
 * Mid-session kick to login. Debounced log (`auth.session_kicked`) so a 401 burst
 * does not spam desktop.jsonl — cold-start still uses `auth.bootstrap`.
 */
export function notifyUnauthorized(fields?: Record<string, unknown>): void {
  const now = Date.now();
  if (now - lastSessionKickAt > 5_000) {
    lastSessionKickAt = now;
    logEvent("warn", "auth.session_kicked", fields);
  }
  onUnauthorized?.();
}

let csrfToken: string | null = null;

/**
 * Absorb an `X-CSRF-Token` off any response that carries one.
 *
 * The server hands it out on the handshake (login / refresh / the cold-start
 * `/me` probe) and on the 403 that rejects a session holding no usable token —
 * nothing else. Reading it unconditionally is what makes that 403 recoverable:
 * every token-bearing write now replays the rejected request itself off the
 * shared {@link isReplayableCsrfRejection} verdict — `api.*`, the workspace file
 * client (`workspaceHttp.authedFetch`), the POST-SSE turn/handoff streams and the
 * raw-bytes avatar upload alike. What is left calling `fetch` directly are the
 * GET streams, which carry no token at all and only arm the *next* write. A path
 * that reads the body but drops the headers leaves the client unarmed and 403ing
 * every write on a live session, which reads to the user as "the app ignores my
 * clicks".
 */
export function captureCsrf(response: Pick<Response, "headers">): void {
  const token = response.headers.get("X-CSRF-Token");
  if (token) csrfToken = token;
}

export function clearCsrfToken(): void {
  csrfToken = null;
}

/** Attach to raw ``fetch`` calls that bypass ``api.*`` (SSE, uploads, …). */
export function getCsrfHeaders(method = "POST"): Record<string, string> {
  return csrfHeaders(method);
}

function csrfHeaders(method: string): Record<string, string> {
  if (!csrfToken) return {};
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return {};
  return { "X-CSRF-Token": csrfToken };
}

/**
 * A 403 the server itself re-armed us against — replaying it is the whole fix.
 *
 * The token lives only in this module's memory, so the first write of a cold
 * start has none to send and eats a `CSRF_FAILED`. That rejection happens in
 * middleware, before any handler runs, and carries a usable token that
 * {@link captureCsrf} has already absorbed — so the same request sent again
 * succeeds, with no risk of doubling a side effect the server never performed.
 * Without the replay the user pays for that with one failed click per launch.
 *
 * A rejection that carries **no** token is the server deliberately declining to
 * re-arm us: the presented token was signed for a different session, so a replay
 * would only succeed as *that* session. Those must keep failing. The header's
 * presence is the backend's own "this is self-healable" verdict — the client
 * never second-guesses it from the status or the message.
 *
 * Exported as the single source of that verdict: the raw-bytes file client
 * (`workspaceHttp.authedFetch`) sends differently but must decide identically,
 * so it reuses this instead of re-deriving an equivalent check that can drift.
 */
export function isReplayableCsrfRejection(
  response: Response,
  error: ApiError,
): boolean {
  return (
    error.status === 403 &&
    error.code === "CSRF_FAILED" &&
    response.headers.get("X-CSRF-Token") !== null
  );
}

// Invoked when a request looks like a backend outage (transport failure or 5xx)
// so the app can confirm via /readyz and switch to a retry screen mid-session,
// the same way it does on startup. Registered by the auth gate.
let onServiceUnavailable: (() => void) | null = null;

export function setServiceUnavailableHandler(
  handler: (() => void) | null,
): void {
  onServiceUnavailable = handler;
}

/** Invoked after a successful silent token refresh. */
let onSessionRenewed: (() => void) | null = null;

export function setSessionRenewedHandler(handler: (() => void) | null): void {
  onSessionRenewed = handler;
}

const isAuthPath = (path: string): boolean => path.startsWith("/v1/auth/");

// A single in-flight refresh shared by every 401'd caller. The refresh token
// rotates on first use, so concurrent requests must NOT each POST /refresh: the
// 2nd would present an already-rotated token, the backend's reuse detection would
// revoke the whole family, and the user would be logged out mid-session
// (认证与会话.md §五/§七). Collapsing the burst into one promise guarantees a
// single rotation; the backend grace window is the cross-window backstop.
let refreshInFlight: Promise<AuthRefreshResult> | null = null;

/**
 * Attempt a single token refresh; three-state so transient outages never look
 * like session death.
 *
 * Single-flight: concurrent callers share one /refresh round-trip (see
 * {@link refreshInFlight}). Exported so non-`api` callers (the raw-fetch SSE
 * stream, the workspace/handoff/realtime channels) reuse the exact same
 * refresh-once policy *and* the same dedup, instead of each racing a rotation.
 */
export function tryRefresh(): Promise<AuthRefreshResult> {
  // D4: when Electron main owns refresh single-flight, delegate so writebacker
  // and renderer never rotate the same refresh family concurrently.
  const outboxRefresh =
    typeof globalThis !== "undefined" &&
    "window" in globalThis &&
    (
      globalThis as {
        window?: {
          outboxApi?: { authRefresh?: () => Promise<AuthRefreshResult> };
        };
      }
    ).window?.outboxApi?.authRefresh;
  if (outboxRefresh) {
    return outboxRefresh().then((outcome) => {
      if (outcome === "renewed") onSessionRenewed?.();
      return outcome;
    });
  }
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async (): Promise<AuthRefreshResult> => {
    try {
      if (isBearerAuth()) {
        const tokens = getBearerTokens();
        if (!tokens) return "auth_dead";
        const res = await fetchWithTimeout(
          `${BASE_URL}/v1/auth/token/refresh`,
          {
            method: "POST",
            credentials: "omit",
            headers: {
              "Content-Type": "application/json",
              ...clientHeaders(),
            },
            body: JSON.stringify({ refresh_token: tokens.refresh_token }),
          },
        );
        if (res.ok) {
          const data = (await res.json()) as {
            access_token?: string;
            refresh_token?: string;
          };
          if (data.access_token && data.refresh_token) {
            setBearerTokens({
              access_token: data.access_token,
              refresh_token: data.refresh_token,
            });
          }
          onSessionRenewed?.();
          return "renewed";
        }
        if (res.status === 401 || res.status === 403) {
          logEvent("warn", "auth.refresh", {
            result: "auth_dead",
            via: "bearer",
            status: res.status,
            message: await peekAuthErrorMessage(res),
          });
          return "auth_dead";
        }
        return "transient";
      }
      const res = await fetchWithTimeout(`${BASE_URL}/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      captureCsrf(res);
      if (res.ok) {
        onSessionRenewed?.();
        return "renewed";
      }
      if (res.status === 401 || res.status === 403) {
        logEvent("warn", "auth.refresh", {
          result: "auth_dead",
          via: "cookie",
          status: res.status,
          message: await peekAuthErrorMessage(res),
        });
        return "auth_dead";
      }
      return "transient";
    } catch {
      return "transient";
    }
  })().finally(() => {
    // Let the next expiry start a fresh refresh once this one has settled.
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** Best-effort server error text for product logs (never tokens). */
async function peekAuthErrorMessage(
  res: Response,
): Promise<string | undefined> {
  try {
    const source = typeof res.clone === "function" ? res.clone() : res;
    const body: unknown = await source.json();
    if (!body || typeof body !== "object") return undefined;
    const err = (body as { error?: unknown }).error;
    const raw =
      typeof err === "string"
        ? err
        : err && typeof err === "object"
          ? (err as { message?: unknown }).message
          : (body as { message?: unknown }).message;
    return typeof raw === "string" ? raw.slice(0, 120) : undefined;
  } catch {
    return undefined;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  retry = false,
  timeoutMs?: number,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const method = (options.method ?? "GET").toUpperCase();
  // `headers` is merged AFTER spreading `options`: caller headers must add to the
  // defaults, never replace the object wholesale — a single `headers` in the
  // options would otherwise silently drop CSRF + Content-Type + client build info.
  const fetchInit: RequestInit = {
    credentials: sessionCredentials(),
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
      ...bearerAuthHeader(),
      ...(isBearerAuth() ? {} : csrfHeaders(method)),
      ...options.headers,
    },
  };
  let response: Response;
  try {
    response =
      timeoutMs != null
        ? await fetchWithTimeout(url, fetchInit, timeoutMs)
        : await fetch(url, fetchInit);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    if (options.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    // fetch only rejects on transport failure (the server never answered), so
    // surface a typed NetworkError the bootstrap can treat as an outage.
    if (!isAuthPath(path)) onServiceUnavailable?.();
    throw new NetworkError(cause);
  }

  captureCsrf(response);

  if (response.ok) {
    // 204 / empty bodies (e.g. notice dismiss) must not call json().
    if (
      response.status === 204 ||
      response.headers.get("content-length") === "0"
    ) {
      return undefined as T;
    }
    const text = await response.text();
    if (!text) return undefined as T;
    return JSON.parse(text) as T;
  }

  // Access token likely expired: refresh once and replay. Auth endpoints opt
  // out so login failures and the refresh call itself never recurse.
  // Three-state: only `auth_dead` drops to login; `transient` uses the outage gate.
  if (response.status === 401 && !isAuthPath(path)) {
    if (!retry) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        // Carry the deadline into the replay: a bootstrapRequest that loses it
        // strands the auth gate on "加载中…" against a hung backend, which is the
        // exact failure BOOTSTRAP_TIMEOUT_MS exists to bound.
        return request<T>(path, options, true, timeoutMs);
      }
      if (outcome === "transient") {
        onServiceUnavailable?.();
      } else {
        notifyUnauthorized({ reason: "refresh_auth_dead", path });
      }
    } else {
      notifyUnauthorized({ reason: "replay_still_401", path });
    }
  }

  // A 5xx means the server is reachable but broken; flag a possible outage so
  // the gate can confirm via /readyz and drop to the retry screen. Auth paths
  // opt out — the bootstrap flow already diagnoses those explicitly.
  if (response.status >= 500 && !isAuthPath(path)) {
    onServiceUnavailable?.();
  }

  const error = new ApiError(
    response.status,
    await response.text(),
    response.headers,
  );

  // Missing or stale CSRF token, and the rejection handed back a fresh one: send
  // the request again carrying it. Bounded by the same `retry` flag as the 401
  // replay above, so a server that keeps rejecting costs one extra attempt, never
  // a loop. Unlike the refresh, this needs no auth-path opt-out — resending the
  // identical request cannot recurse.
  if (!retry && isReplayableCsrfRejection(response, error)) {
    logEvent("info", "auth.csrf_replay", { path, method });
    return request<T>(path, options, true, timeoutMs);
  }

  throw error;
}

/** Auth-gate bootstrap REST calls — same as {@link request} but bounded in time. */
export function bootstrapRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  return request<T>(path, options, false, BOOTSTRAP_TIMEOUT_MS);
}

/**
 * Like {@link request}, but also returns the HTTP status (for idempotent
 * create-or-reuse endpoints that distinguish 200 vs 201).
 */
async function requestWithStatus<T>(
  path: string,
  options: RequestInit = {},
  retry = false,
): Promise<{ data: T; status: number }> {
  const url = `${BASE_URL}${path}`;
  const method = (options.method ?? "GET").toUpperCase();
  // Caller headers merge into the defaults — see {@link request}.
  const fetchInit: RequestInit = {
    credentials: sessionCredentials(),
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...clientHeaders(),
      ...bearerAuthHeader(),
      ...(isBearerAuth() ? {} : csrfHeaders(method)),
      ...options.headers,
    },
  };
  let response: Response;
  try {
    response = await fetch(url, fetchInit);
  } catch (cause) {
    if (!isAuthPath(path)) onServiceUnavailable?.();
    throw new NetworkError(cause);
  }

  captureCsrf(response);

  if (response.ok) {
    if (
      response.status === 204 ||
      response.headers.get("content-length") === "0"
    ) {
      return { data: undefined as T, status: response.status };
    }
    const text = await response.text();
    if (!text) return { data: undefined as T, status: response.status };
    return { data: JSON.parse(text) as T, status: response.status };
  }

  if (response.status === 401 && !isAuthPath(path)) {
    if (!retry) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        return requestWithStatus<T>(path, options, true);
      }
      if (outcome === "transient") {
        onServiceUnavailable?.();
      } else {
        notifyUnauthorized({ reason: "refresh_auth_dead", path });
      }
    } else {
      notifyUnauthorized({ reason: "replay_still_401", path });
    }
  }

  if (response.status >= 500 && !isAuthPath(path)) {
    onServiceUnavailable?.();
  }

  const error = new ApiError(
    response.status,
    await response.text(),
    response.headers,
  );

  // Same one-shot CSRF replay as {@link request} — see the rationale there.
  if (!retry && isReplayableCsrfRejection(response, error)) {
    logEvent("info", "auth.csrf_replay", { path, method });
    return requestWithStatus<T>(path, options, true);
  }

  throw error;
}

export const api = {
  get: <T>(path: string, init?: Pick<RequestInit, "signal">) =>
    request<T>(path, init ?? {}),

  post: <T>(path: string, body?: unknown, timeoutMs?: number) =>
    request<T>(
      path,
      {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      },
      false,
      timeoutMs,
    ),

  /** POST that surfaces status (200 reuse vs 201 create). */
  postWithStatus: <T>(path: string, body?: unknown) =>
    requestWithStatus<T>(path, {
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

  delete: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "DELETE",
      body: body ? JSON.stringify(body) : undefined,
    }),
};
