/**
 * Main-process Bearer auth client tests (as-built: 认证与会话 §七).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Subset of Electron Cookie fields used by production `readAuthCookies`. */
type MockCookie = { name: string; value: string; path?: string };

const h = vi.hoisted(() => {
  const cookies = new Map<string, string>();
  return {
    cookies,
    fetchMock: vi.fn(),
    cookieGet: vi.fn(
      async (): Promise<MockCookie[]> =>
        [...cookies.entries()].map(([name, value]) => ({ name, value })),
    ),
    cookieSet: vi.fn(
      async (details: {
        name: string;
        value: string;
        path?: string;
        sameSite?: string;
        secure?: boolean;
        expirationDate?: number;
      }) => {
        cookies.set(details.name, details.value);
      },
    ),
    cookieFlush: vi.fn(async () => {}),
    appOn: vi.fn(),
    appQuit: vi.fn(),
  };
});

vi.mock("electron", () => ({
  net: { fetch: h.fetchMock },
  app: { on: h.appOn, quit: h.appQuit },
  session: {
    defaultSession: {
      cookies: {
        get: h.cookieGet,
        set: h.cookieSet,
        flushStore: h.cookieFlush,
      },
    },
  },
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

// Bake a local API base the same way electron-vite define would.
vi.stubGlobal("__API_BASE_URL__", "http://localhost:8000");

import {
  bearerPostJson,
  deriveAuthCookieAttrs,
  persistAuthCookies,
  refreshAccessToken,
  resetAuthClientForTests,
} from "../auth-client";
import { logDesktop } from "../log-service";

beforeEach(() => {
  h.cookies.clear();
  h.fetchMock.mockReset();
  h.cookieGet.mockClear();
  h.cookieSet.mockClear();
  h.cookieFlush.mockClear();
  vi.mocked(logDesktop).mockClear();
  resetAuthClientForTests();
});

afterEach(() => {
  resetAuthClientForTests();
});

describe("deriveAuthCookieAttrs", () => {
  it("uses lax + insecure for http (dev localhost)", () => {
    expect(deriveAuthCookieAttrs("http://localhost:8000")).toEqual({
      secure: false,
      sameSite: "lax",
    });
  });

  it("uses no_restriction + secure for https (prod)", () => {
    expect(deriveAuthCookieAttrs("https://api.example.com")).toEqual({
      secure: true,
      sameSite: "no_restriction",
    });
  });
});

describe("refreshAccessToken (Bearer body refresh)", () => {
  it("POSTs /v1/auth/token/refresh without Cookie and writes new tokens", async () => {
    h.cookies.set("refresh_token", "old-refresh");
    h.fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "new-access",
        refresh_token: "new-refresh",
        expires_in: 3600,
        refresh_expires_in: 30 * 86400,
      }),
    });

    expect(await refreshAccessToken()).toBe("renewed");
    expect(h.fetchMock).toHaveBeenCalledOnce();
    const [url, init] = h.fetchMock.mock.calls[0] as [
      string,
      { credentials?: string; headers: Record<string, string>; body: string },
    ];
    expect(url).toBe("http://localhost:8000/v1/auth/token/refresh");
    // Pure Bearer: Electron would coerce default credentials to `include` (no origin
    // in the main process) and attach session cookies — omit is what keeps it cookie-less.
    expect(init.credentials).toBe("omit");
    expect(init.headers.Authorization).toBeUndefined();
    expect(init.headers.Cookie).toBeUndefined();
    expect(JSON.parse(init.body)).toEqual({ refresh_token: "old-refresh" });
    expect(h.cookies.get("access_token")).toBe("new-access");
    expect(h.cookies.get("refresh_token")).toBe("new-refresh");

    // http API → lax + insecure (Chromium rejects SameSite=None without Secure)
    const accessSet = h.cookieSet.mock.calls.find(
      (c) => (c[0] as { name: string }).name === "access_token",
    )?.[0] as {
      sameSite: string;
      secure: boolean;
      expirationDate?: number;
    };
    const refreshSet = h.cookieSet.mock.calls.find(
      (c) => (c[0] as { name: string }).name === "refresh_token",
    )?.[0] as {
      sameSite: string;
      secure: boolean;
      expirationDate?: number;
    };
    expect(accessSet.sameSite).toBe("lax");
    expect(accessSet.secure).toBe(false);
    expect(refreshSet.sameSite).toBe("lax");
    expect(refreshSet.secure).toBe(false);
    expect(accessSet.expirationDate).toBeGreaterThan(
      Math.floor(Date.now() / 1000),
    );
    expect(refreshSet.expirationDate).toBeGreaterThan(
      Math.floor(Date.now() / 1000) + 29 * 86400,
    );
    expect(h.cookieFlush).toHaveBeenCalled();
  });

  it("falls back to 30d refresh expirationDate when field omitted", async () => {
    h.cookies.set("refresh_token", "old-refresh");
    const before = Math.floor(Date.now() / 1000);
    h.fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "a",
        refresh_token: "r",
        expires_in: 60,
      }),
    });
    expect(await refreshAccessToken()).toBe("renewed");
    const refreshSet = h.cookieSet.mock.calls.find(
      (c) => (c[0] as { name: string }).name === "refresh_token",
    )?.[0] as { expirationDate: number };
    expect(refreshSet.expirationDate).toBeGreaterThanOrEqual(
      before + 30 * 86400,
    );
    expect(refreshSet.expirationDate).toBeLessThanOrEqual(
      before + 30 * 86400 + 5,
    );
  });

  it("single-flights concurrent refresh callers", async () => {
    h.cookies.set("refresh_token", "r1");
    let resolveFetch!: (v: unknown) => void;
    h.fetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    const a = refreshAccessToken();
    const b = refreshAccessToken();
    resolveFetch({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "a",
        refresh_token: "r2",
        expires_in: 60,
        refresh_expires_in: 86400,
      }),
    });
    expect(await Promise.all([a, b])).toEqual(["renewed", "renewed"]);
    expect(h.fetchMock).toHaveBeenCalledOnce();
  });

  it("returns auth_dead when refresh cookie is missing", async () => {
    expect(await refreshAccessToken()).toBe("auth_dead");
    expect(h.fetchMock).not.toHaveBeenCalled();
    expect(logDesktop).toHaveBeenCalledWith(
      expect.objectContaining({
        level: "warn",
        event: "auth.refresh",
        fields: expect.objectContaining({
          result: "auth_dead",
          reason: "missing_refresh_cookie",
        }),
      }),
    );
  });

  it("prefers refresh cookie whose path matches the API prefix", async () => {
    // Stale path first — naive find() would rotate the wrong tip.
    h.cookieGet.mockResolvedValueOnce([
      { name: "refresh_token", value: "stale", path: "/api/v1/auth" },
      { name: "refresh_token", value: "fresh", path: "/v1/auth" },
    ]);
    h.fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "a",
        refresh_token: "r2",
        expires_in: 60,
        refresh_expires_in: 86400,
      }),
    });
    expect(await refreshAccessToken()).toBe("renewed");
    expect(JSON.parse(h.fetchMock.mock.calls[0][1].body)).toEqual({
      refresh_token: "fresh",
    });
    expect(logDesktop).toHaveBeenCalledWith(
      expect.objectContaining({
        event: "auth.refresh",
        fields: expect.objectContaining({
          result: "ambiguous_refresh_cookies",
          prefer: "/v1/auth",
        }),
      }),
    );
  });

  it("returns auth_dead on 401/403", async () => {
    h.cookies.set("refresh_token", "r1");
    h.fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({
        error: { code: "AUTH_ERROR", message: "Refresh token reuse detected" },
      }),
    });
    expect(await refreshAccessToken()).toBe("auth_dead");
    expect(logDesktop).toHaveBeenCalledWith(
      expect.objectContaining({
        event: "auth.refresh",
        fields: expect.objectContaining({
          result: "auth_dead",
          reason: "http",
          status: 401,
          message: "Refresh token reuse detected",
        }),
      }),
    );
  });

  it("returns transient on network error", async () => {
    h.cookies.set("refresh_token", "r1");
    h.fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    expect(await refreshAccessToken()).toBe("transient");
  });

  it("returns transient on 5xx / 429", async () => {
    h.cookies.set("refresh_token", "r1");
    h.fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({ error: "down" }),
    });
    expect(await refreshAccessToken()).toBe("transient");
  });

  it("returns transient when cookie write fails after server rotation", async () => {
    h.cookies.set("refresh_token", "r1");
    h.fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "a",
        refresh_token: "r2",
        expires_in: 60,
        refresh_expires_in: 86400,
      }),
    });
    h.cookieSet.mockRejectedValueOnce(new Error("Chromium rejected cookie"));
    expect(await refreshAccessToken()).toBe("transient");
  });
});

describe("bearerPostJson", () => {
  it("sends Authorization Bearer and no Cookie; retries once after 401 refresh", async () => {
    h.cookies.set("access_token", "expired");
    h.cookies.set("refresh_token", "r1");
    h.fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ error: "expired" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          access_token: "fresh",
          refresh_token: "r2",
          expires_in: 60,
          refresh_expires_in: 86400,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ user_message_id: "u1" }),
      });

    const result = await bearerPostJson("/v1/conversations/c1/local-turns", {
      user_message: "hi",
      user_message_id: "u1",
    });
    expect(result.ok).toBe(true);
    expect(result.body).toEqual({ user_message_id: "u1" });

    // 1) original POST, 2) token refresh, 3) retry POST
    expect(h.fetchMock).toHaveBeenCalledTimes(3);
    const firstPost = h.fetchMock.mock.calls[0] as [
      string,
      { credentials?: string; headers: Record<string, string> },
    ];
    // `credentials: "omit"` guards the server's pure-Bearer CSRF exemption
    // (middleware/csrf.py): an attached access_token cookie would 403 the write-back.
    expect(firstPost[1].credentials).toBe("omit");
    expect(firstPost[1].headers.Authorization).toBe("Bearer expired");
    expect(firstPost[1].headers.Cookie).toBeUndefined();
    const retryPost = h.fetchMock.mock.calls[2] as [
      string,
      { headers: Record<string, string> },
    ];
    expect(retryPost[1].headers.Authorization).toBe("Bearer fresh");
  });
});

describe("persistAuthCookies", () => {
  it("stamps session cookies with expirationDate and flushes", async () => {
    h.cookies.set("access_token", "a");
    h.cookies.set("refresh_token", "r");
    const before = Math.floor(Date.now() / 1000);
    await persistAuthCookies();
    const refreshSet = h.cookieSet.mock.calls.find(
      (c) => (c[0] as { name: string }).name === "refresh_token",
    )?.[0] as { expirationDate: number; path: string };
    expect(refreshSet.expirationDate).toBeGreaterThanOrEqual(
      before + 30 * 86400,
    );
    expect(refreshSet.path).toBe("/v1/auth");
    expect(h.cookieFlush).toHaveBeenCalled();
  });

  it("is a no-op when the jar is empty", async () => {
    await persistAuthCookies();
    expect(h.cookieSet).not.toHaveBeenCalled();
    expect(h.cookieFlush).not.toHaveBeenCalled();
  });
});
