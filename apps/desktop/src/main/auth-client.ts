/**
 * Main-process first-class API client (as-built: 认证与会话 §七 / §五).
 *
 * Pure Bearer: reads access_token from the session cookie jar, sends
 * `Authorization: Bearer …` and **never** attaches Cookie headers (CSRF exempt
 * only when Bearer is present AND no access_token cookie — middleware/csrf.py).
 *
 * Refresh: `POST /v1/auth/token/refresh` (body) → write new tokens back into
 * cookies so the renderer stays in sync. Single-flight across writeback +
 * renderer IPC so refresh-family rotation never double-fires.
 */
import type { components } from "@agentcore/contract-rest-types";
import { net, app, session } from "electron";
import type { AuthRefreshResult } from "../shared/outbox-contract";
import { logDesktop } from "./log-service";

type TokenResponse = components["schemas"]["TokenResponse"];

const ACCESS_COOKIE = "access_token";
const REFRESH_COOKIE = "refresh_token";
/** Fallback when bearer TokenResponse omits `expires_in`. */
const DEFAULT_ACCESS_EXPIRES_SEC = 30 * 60;
/** Fallback when bearer TokenResponse omits `refresh_expires_in` (older servers). */
const DEFAULT_REFRESH_EXPIRES_SEC = 30 * 86400;
/** Don't block app quit if Chromium's cookie sqlite flush hangs. */
const QUIT_FLUSH_TIMEOUT_MS = 2000;

declare const __API_BASE_URL__: string;

/** Full API base including path prefix (e.g. `https://host/api` or `http://localhost:8000`). */
export function apiBase(): string {
  try {
    return String(__API_BASE_URL__).replace(/\/$/, "");
  } catch {
    return "http://localhost:8000";
  }
}

export function apiOrigin(): string {
  try {
    return new URL(apiBase()).origin;
  } catch {
    return "http://localhost:8000";
  }
}

/** Path prefix baked into the API base (e.g. `/api` in prod; empty in local dev). */
export function apiPathPrefix(): string {
  try {
    const path = new URL(apiBase()).pathname.replace(/\/$/, "");
    return path === "/" ? "" : path;
  } catch {
    return "";
  }
}

/**
 * Derive cookie SameSite/Secure from the API URL.
 * https → None+Secure (prod cross-site); http → Lax+insecure (dev localhost).
 */
export function deriveAuthCookieAttrs(cookieUrl: string): {
  secure: boolean;
  sameSite: "lax" | "no_restriction";
} {
  const secure = cookieUrl.startsWith("https:");
  return {
    secure,
    sameSite: secure ? "no_restriction" : "lax",
  };
}

function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${apiBase()}${p}`;
}

async function readAuthCookies(): Promise<{
  access_token?: string;
  refresh_token?: string;
}> {
  const all = await session.defaultSession.cookies.get({});
  const access = all.find((c) => c.name === ACCESS_COOKIE)?.value;
  // Prefer the path that matches our API prefix. Same name at a stale path
  // (e.g. `/v1/auth` left from a prior local-dev jar) can be an already-rotated
  // tip — presenting it past the reuse grace revokes the whole family.
  const refreshPath = refreshCookiePath();
  const refreshNamed = all.filter((c) => c.name === REFRESH_COOKIE);
  if (refreshNamed.length > 1) {
    logDesktop({
      level: "warn",
      event: "auth.refresh",
      fields: {
        result: "ambiguous_refresh_cookies",
        count: refreshNamed.length,
        paths: refreshNamed.map((c) => c.path ?? ""),
        prefer: refreshPath,
      },
    });
  }
  const refresh =
    refreshNamed.find((c) => (c.path ?? "") === refreshPath)?.value ??
    refreshNamed[0]?.value;
  return { access_token: access, refresh_token: refresh };
}

function cookieUrl(): string {
  // Cookie URL must match the API origin so Chromium stores them for that host.
  return apiOrigin();
}

function refreshCookiePath(): string {
  // Mirror server `_refresh_cookie_path`: path-scoped to auth refresh endpoints.
  const prefix = apiPathPrefix();
  return `${prefix}/v1/auth`;
}

async function writeAuthCookies(tokens: {
  access_token: string;
  refresh_token: string;
  expires_in?: number;
  refresh_expires_in?: number;
}): Promise<void> {
  const url = cookieUrl();
  const { secure, sameSite } = deriveAuthCookieAttrs(url);
  const nowSec = Math.floor(Date.now() / 1000);
  // Always stamp expirationDate — omitting it makes a session cookie that
  // Chromium drops when the Electron process exits (reopen → login screen).
  const accessExpiry =
    nowSec +
    (typeof tokens.expires_in === "number"
      ? tokens.expires_in
      : DEFAULT_ACCESS_EXPIRES_SEC);
  const refreshExpiry =
    nowSec + (tokens.refresh_expires_in ?? DEFAULT_REFRESH_EXPIRES_SEC);
  await session.defaultSession.cookies.set({
    url,
    name: ACCESS_COOKIE,
    value: tokens.access_token,
    path: "/",
    httpOnly: true,
    secure,
    sameSite,
    expirationDate: accessExpiry,
  });
  await session.defaultSession.cookies.set({
    url,
    name: REFRESH_COOKIE,
    value: tokens.refresh_token,
    path: refreshCookiePath(),
    httpOnly: true,
    secure,
    sameSite,
    expirationDate: refreshExpiry,
  });
  await flushAuthCookieStore();
}

/** Best-effort cookie sqlite flush. Failures must not look like session death. */
export async function flushAuthCookieStore(): Promise<void> {
  try {
    await session.defaultSession.cookies.flushStore();
  } catch (err) {
    logDesktop({
      level: "warn",
      event: "auth.persist",
      fields: {
        result: "flush_failed",
        message: String(err).slice(0, 120),
      },
    });
  }
}

/**
 * Re-stamp HTTP Set-Cookie auth cookies with expirationDate + SameSite/Secure
 * that Electron will persist, then flush to disk. Renderer login relies on
 * Chromium accepting Set-Cookie from the API host; those can land as session
 * cookies (no Max-Age from the jar's POV, or never flushed). Calling this
 * after login (and on cold-start /me ok) is what keeps reopen logged in.
 */
export async function persistAuthCookies(): Promise<void> {
  const all = await session.defaultSession.cookies.get({});
  const access = all.find((c) => c.name === ACCESS_COOKIE);
  const refreshPath = refreshCookiePath();
  const refreshNamed = all.filter((c) => c.name === REFRESH_COOKIE);
  const refresh =
    refreshNamed.find((c) => (c.path ?? "") === refreshPath) ?? refreshNamed[0];
  const url = cookieUrl();
  const { secure, sameSite } = deriveAuthCookieAttrs(url);
  const nowSec = Math.floor(Date.now() / 1000);

  const writes: Promise<void>[] = [];
  if (access?.value) {
    const accessExpiry =
      typeof access.expirationDate === "number" &&
      access.expirationDate > nowSec
        ? access.expirationDate
        : nowSec + DEFAULT_ACCESS_EXPIRES_SEC;
    writes.push(
      session.defaultSession.cookies.set({
        url,
        name: ACCESS_COOKIE,
        value: access.value,
        path: "/",
        httpOnly: true,
        secure,
        sameSite,
        expirationDate: accessExpiry,
      }),
    );
  }
  if (refresh?.value) {
    const refreshExpiry =
      typeof refresh.expirationDate === "number" &&
      refresh.expirationDate > nowSec
        ? refresh.expirationDate
        : nowSec + DEFAULT_REFRESH_EXPIRES_SEC;
    writes.push(
      session.defaultSession.cookies.set({
        url,
        name: REFRESH_COOKIE,
        value: refresh.value,
        path: refreshCookiePath(),
        httpOnly: true,
        secure,
        sameSite,
        expirationDate: refreshExpiry,
      }),
    );
  }
  if (writes.length === 0) return;
  await Promise.all(writes);
  await flushAuthCookieStore();
  logDesktop({
    level: "info",
    event: "auth.persist",
    fields: {
      result: "stamped",
      access: Boolean(access?.value),
      refresh: Boolean(refresh?.value),
    },
  });
}

let quitFlushHooked = false;

/** Wait for cookie sqlite flush on quit so a fast exit after login doesn't drop the jar. */
export function installAuthCookieFlushOnQuit(): void {
  if (quitFlushHooked) return;
  quitFlushHooked = true;
  let flushed = false;
  app.on("before-quit", (event) => {
    if (flushed) return;
    event.preventDefault();
    const timeout = new Promise<void>((resolve) => {
      setTimeout(resolve, QUIT_FLUSH_TIMEOUT_MS);
    });
    void Promise.race([flushAuthCookieStore(), timeout]).finally(() => {
      flushed = true;
      app.quit();
    });
  });
}

let refreshInFlight: Promise<AuthRefreshResult> | null = null;

/**
 * Rotate tokens via body refresh; single-flight for main + renderer callers.
 * Three-state so transient outages are never mistaken for session death.
 */
export function refreshAccessToken(): Promise<AuthRefreshResult> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async (): Promise<AuthRefreshResult> => {
    const cookies = await readAuthCookies();
    const refresh = cookies.refresh_token?.trim();
    if (!refresh) {
      logDesktop({
        level: "warn",
        event: "auth.refresh",
        fields: { result: "auth_dead", reason: "missing_refresh_cookie" },
      });
      return "auth_dead";
    }
    let res: Response;
    try {
      // `credentials: "omit"` is REQUIRED (not cosmetic): a main-process net.fetch has
      // no document origin, so Electron coerces the default `same-origin` credentials to
      // `include` (electron/lib/browser/api/net-fetch.ts) and would attach defaultSession
      // cookies. This must stay a pure Bearer client — refresh travels in the body only.
      res = await net.fetch(apiUrl("/v1/auth/token/refresh"), {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
    } catch {
      return "transient";
    }
    if (res.status === 401 || res.status === 403) {
      logDesktop({
        level: "warn",
        event: "auth.refresh",
        fields: {
          result: "auth_dead",
          reason: "http",
          status: res.status,
          message: await peekAuthErrorMessage(res),
        },
      });
      return "auth_dead";
    }
    if (!res.ok) return "transient";
    let body: TokenResponse;
    try {
      body = (await res.json()) as TokenResponse;
    } catch {
      return "transient";
    }
    if (!body.access_token || !body.refresh_token) return "transient";
    try {
      await writeAuthCookies({
        access_token: body.access_token,
        refresh_token: body.refresh_token,
        expires_in: body.expires_in,
        refresh_expires_in: body.refresh_expires_in ?? undefined,
      });
    } catch {
      // Server already rotated; local jar write failed — retry later, don't logout.
      logDesktop({
        level: "warn",
        event: "auth.refresh",
        fields: { result: "transient", reason: "cookie_write_failed" },
      });
      return "transient";
    }
    return "renewed";
  })().finally(() => {
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

export interface BearerJsonResult {
  ok: boolean;
  status: number;
  body: unknown;
}

/**
 * POST JSON with pure Bearer auth (no Cookie header). On 401, refresh once and retry.
 */
export async function bearerPostJson(
  path: string,
  body: unknown,
): Promise<BearerJsonResult> {
  // `credentials: "omit"` is REQUIRED, not cosmetic: a main-process net.fetch has no
  // document origin, so Electron coerces the default `same-origin` credentials to
  // `include` (electron/lib/browser/api/net-fetch.ts) and would attach the defaultSession
  // `access_token` cookie. That cookie breaks the server's pure-Bearer CSRF exemption
  // (middleware/csrf.py: exempt only when Bearer present AND no access_token cookie) →
  // a 403 on every write-back. Omit keeps this a true cookie-less Bearer client.
  const doFetch = async (access: string): Promise<Response> =>
    net.fetch(apiUrl(path), {
      method: "POST",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${access}`,
      },
      body: JSON.stringify(body),
    });

  const cookies = await readAuthCookies();
  let access = cookies.access_token?.trim();
  if (!access) {
    const refreshed = await refreshAccessToken();
    if (refreshed !== "renewed") {
      return { ok: false, status: 401, body: { error: "missing_token" } };
    }
    access = (await readAuthCookies()).access_token?.trim();
    if (!access) {
      return { ok: false, status: 401, body: { error: "missing_token" } };
    }
  }

  let res = await doFetch(access);
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed === "renewed") {
      access = (await readAuthCookies()).access_token?.trim();
      if (access) res = await doFetch(access);
    }
  }

  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  return { ok: res.ok, status: res.status, body: parsed };
}

/**
 * GET (or custom-method) with pure Bearer auth (no Cookie header); on 401, refresh
 * once and retry. Returns the **raw** {@link Response} (caller streams bytes + reads
 * headers) — used by the `workspace://` protocol proxy to relay 工作区 file bytes
 * （`/v1/workspaces/{wsId}/files/…`）without ever exposing the access token to the
 * renderer or the workspace session.
 *
 * `credentials: "omit"` is REQUIRED (same reason as {@link bearerPostJson}): a
 * main-process net.fetch has no document origin, so Electron would coerce the default
 * to `include` and attach defaultSession cookies — this must stay a cookie-less Bearer
 * client. A missing/dead token surfaces as a synthetic 401 Response (never throws for
 * auth), so the protocol handler renders a clean 401 instead of a broken page.
 */
export async function bearerFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const doFetch = async (access: string): Promise<Response> =>
    net.fetch(apiUrl(path), {
      ...init,
      credentials: "omit",
      headers: { ...init.headers, Authorization: `Bearer ${access}` },
    });

  const cookies = await readAuthCookies();
  let access = cookies.access_token?.trim();
  if (!access) {
    const refreshed = await refreshAccessToken();
    if (refreshed !== "renewed") return new Response(null, { status: 401 });
    access = (await readAuthCookies()).access_token?.trim();
    if (!access) return new Response(null, { status: 401 });
  }

  let res = await doFetch(access);
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed === "renewed") {
      access = (await readAuthCookies()).access_token?.trim();
      if (access) res = await doFetch(access);
    }
  }
  return res;
}

/** Test seam: clear in-flight refresh. */
export function resetAuthClientForTests(): void {
  refreshInFlight = null;
}
