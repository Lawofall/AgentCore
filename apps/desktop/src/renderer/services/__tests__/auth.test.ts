import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, BASE_URL, clearCsrfToken } from "../api";
import {
  bootstrapAuth,
  changePassword,
  deleteAccount,
  deleteAvatar,
  fetchMe,
  forgotPassword,
  listSessions,
  logout,
  resetPassword,
  revokeOtherSessions,
  revokeSession,
  sendEmailCode,
  sendRegisterCode,
  updateProfile,
  uploadAvatar,
  verifyEmail,
  verifyRegister,
} from "../auth";

const ME = "/v1/auth/me";
const REFRESH = "/v1/auth/refresh";
const READYZ = "/readyz";

const backendUser = {
  id: "u1",
  username: "dev",
  display_name: "Dev",
  email: null,
  role: "admin",
  created_at: "2024-01-01T00:00:00Z",
};

function json(
  body: unknown,
  status = 200,
  extraHeaders?: Record<string, string>,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });
}

type Handler = (url: string) => Response;

function mockFetch(handler: Handler): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) =>
      Promise.resolve(handler(String(input))),
    ),
  );
}

beforeEach(() => {
  // Force dev auto-login to a no-op so bootstrap exercises only the cookie and
  // health-probe branches deterministically, regardless of any local .env.local.
  vi.stubEnv("VITE_DEV_USERNAME", "");
  vi.stubEnv("VITE_DEV_PASSWORD", "");
  // The CSRF token lives in api.ts module state — reset it so token assertions
  // never depend on what a previous test happened to capture.
  clearCsrfToken();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  clearCsrfToken();
});

describe("bootstrapAuth", () => {
  it("returns authenticated when the session cookie is valid", async () => {
    mockFetch((url) => {
      if (url.endsWith(ME)) return json(backendUser);
      throw new Error(`unexpected fetch: ${url}`);
    });

    const result = await bootstrapAuth();

    expect(result.kind).toBe("authenticated");
    if (result.kind === "authenticated") {
      expect(result.user.username).toBe("dev");
    }
  });

  it("returns unauthenticated on 401 when the backend is ready", async () => {
    mockFetch((url) => {
      if (url.endsWith(REFRESH)) return json({ error: "no session" }, 401);
      if (url.endsWith(ME)) return json({ error: "no session" }, 401);
      if (url.endsWith(READYZ))
        return json({ status: "ready", database: true });
      throw new Error(`unexpected fetch: ${url}`);
    });

    const result = await bootstrapAuth();

    expect(result.kind).toBe("unauthenticated");
  });

  it("silently refreshes an expired access token and stays authenticated", async () => {
    // Cold start with an expired access cookie but a still-valid refresh cookie:
    // /auth/me 401s, the silent refresh succeeds, and the retried /auth/me works.
    let meCalls = 0;
    mockFetch((url) => {
      if (url.endsWith(REFRESH)) return json({ status: "ok" });
      if (url.endsWith(ME)) {
        meCalls += 1;
        return meCalls === 1
          ? json({ error: "access token expired" }, 401)
          : json(backendUser);
      }
      throw new Error(`unexpected fetch: ${url}`);
    });

    const result = await bootstrapAuth();

    expect(result.kind).toBe("authenticated");
    if (result.kind === "authenticated") {
      expect(result.user.username).toBe("dev");
    }
    expect(meCalls).toBe(2); // probed, refreshed, then re-probed successfully
  });

  it("returns unavailable on 401 when /readyz reports the database down", async () => {
    mockFetch((url) => {
      if (url.endsWith(REFRESH)) return json({ error: "no session" }, 401);
      if (url.endsWith(ME)) return json({ error: "no session" }, 401);
      if (url.endsWith(READYZ))
        return json({ status: "not_ready", database: false }, 503);
      throw new Error(`unexpected fetch: ${url}`);
    });

    const result = await bootstrapAuth();

    expect(result.kind).toBe("unavailable");
    if (result.kind === "unavailable") {
      expect(result.reason).toContain("AgentCore");
      expect(result.reason).not.toContain("请确认数据库");
    }
  });

  it("returns unavailable when /auth/me 500s (server reachable but broken)", async () => {
    mockFetch((url) => {
      if (url.endsWith(ME)) return json({ error: "boom" }, 500);
      if (url.endsWith(READYZ))
        return json({ status: "not_ready", database: false }, 503);
      throw new Error(`unexpected fetch: ${url}`);
    });

    const result = await bootstrapAuth();

    expect(result.kind).toBe("unavailable");
  });

  it("returns unavailable when the backend is unreachable (network error)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    const result = await bootstrapAuth();

    expect(result.kind).toBe("unavailable");
    if (result.kind === "unavailable") {
      expect(result.reason).toContain("连不上 AgentCore");
    }
  });

  it("returns unavailable when bootstrap probes time out", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.reject(
          new DOMException("The operation timed out.", "TimeoutError"),
        ),
      ),
    );

    const result = await bootstrapAuth();

    expect(result.kind).toBe("unavailable");
    if (result.kind === "unavailable") {
      expect(result.reason).toContain("连不上 AgentCore");
    }
  });
});

interface Captured {
  url: string;
  method?: string;
  body: unknown;
  headers: Record<string, string>;
}

/** Stub fetch with a per-URL handler, recording each request's headers. */
function recordFetch(handler: Handler): Captured[] {
  const calls: Captured[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method,
        body: init?.body,
        headers: { ...((init?.headers as Record<string, string>) ?? {}) },
      });
      return Promise.resolve(handler(String(input)));
    }),
  );
  return calls;
}

/** Stub fetch, recording each call's url/method/parsed-body and replying with
 *  `response`, so account-ops tests can assert the exact request they sent. */
function captureFetch(response: Response): Captured[] {
  const calls: Captured[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method,
        body:
          typeof init?.body === "string" ? JSON.parse(init.body) : init?.body,
        headers: { ...((init?.headers as Record<string, string>) ?? {}) },
      });
      return Promise.resolve(response.clone());
    }),
  );
  return calls;
}

describe("changePassword", () => {
  it("POSTs current + new password to /auth/change-password", async () => {
    const calls = captureFetch(json({ status: "ok" }));

    await changePassword("old-pw", "brand-new-pw");

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/v1/auth/change-password");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({
      current_password: "old-pw",
      new_password: "brand-new-pw",
    });
  });

  it("rejects with ApiError when the current password is wrong", async () => {
    captureFetch(
      json({ error: { code: "auth", message: "当前密码不正确" } }, 401),
    );

    await expect(changePassword("nope", "brand-new-pw")).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});

describe("updateProfile", () => {
  it("PATCHes only the provided fields and maps the response", async () => {
    const calls = captureFetch(
      json({ ...backendUser, display_name: "New Name" }),
    );

    const user = await updateProfile({ displayName: "New Name" });

    expect(calls[0].url).toContain("/v1/auth/me");
    expect(calls[0].method).toBe("PATCH");
    expect(calls[0].body).toEqual({ display_name: "New Name" });
    expect(user.displayName).toBe("New Name");
  });

  it("PATCHes username when claiming a handle", async () => {
    const calls = captureFetch(json({ ...backendUser, username: "alice" }));

    const user = await updateProfile({ username: "alice" });

    expect(calls[0].body).toEqual({ username: "alice" });
    expect(user.username).toBe("alice");
  });

  it("sends an explicit null to clear the email", async () => {
    const calls = captureFetch(json({ ...backendUser, email: null }));

    await updateProfile({ email: null });

    expect(calls[0].body).toEqual({ email: null });
  });

  it("rejects with ApiError when the email is taken", async () => {
    captureFetch(
      json({ error: { code: "validation", message: "该邮箱已被占用" } }, 422),
    );

    await expect(
      updateProfile({ email: "taken@example.com" }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("deleteAccount", () => {
  it("DELETEs /auth/me with the confirming password", async () => {
    const calls = captureFetch(json({ status: "ok" }));

    await deleteAccount("my-password");

    expect(calls[0].url).toContain("/v1/auth/me");
    expect(calls[0].method).toBe("DELETE");
    expect(calls[0].body).toEqual({ password: "my-password" });
  });
});

describe("uploadAvatar", () => {
  it("POSTs the raw file body and resolves the returned avatar URL", async () => {
    const calls = captureFetch(
      json({ ...backendUser, avatar_url: "/v1/users/u1/avatar?v=abc" }),
    );
    const file = new File([new Uint8Array([1, 2, 3])], "a.png", {
      type: "image/png",
    });

    const user = await uploadAvatar(file);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/v1/users/me/avatar");
    expect(calls[0].method).toBe("POST");
    // Raw bytes, not JSON — the File rides through as the body untouched.
    expect(calls[0].body).toBe(file);
    // Relative server URL gets resolved against the API base for <img src>.
    expect(user.avatarUrl).toBe(`${BASE_URL}/v1/users/u1/avatar?v=abc`);
  });

  it("rejects with ApiError when the image is rejected", async () => {
    captureFetch(
      json({ error: { code: "validation", message: "图片无法解码" } }, 422),
    );
    const file = new File([new Uint8Array([0])], "bad.txt", {
      type: "image/png",
    });

    await expect(uploadAvatar(file)).rejects.toBeInstanceOf(ApiError);
  });

  it("sends the CSRF token — raw bytes are still a mutating cookie request", async () => {
    const calls = recordFetch((url) =>
      url.endsWith("/v1/auth/me")
        ? json(backendUser, 200, { "X-CSRF-Token": "tok-from-me" })
        : json({ ...backendUser, avatar_url: null }),
    );
    await fetchMe(); // hands the client its token, like login does

    await uploadAvatar(
      new File([new Uint8Array([1])], "a.png", { type: "image/png" }),
    );

    const upload = calls[1];
    expect(upload.url).toContain("/v1/users/me/avatar");
    expect(upload.headers["X-CSRF-Token"]).toBe("tok-from-me");
    // …without losing the raw-bytes content type the backend decodes by.
    expect(upload.headers["Content-Type"]).toBe("image/png");
  });

  it("refreshes the CSRF token from its own response headers", async () => {
    const calls = recordFetch((url) =>
      url.endsWith("/avatar")
        ? json({ ...backendUser, avatar_url: null }, 200, {
            "X-CSRF-Token": "tok-rotated",
          })
        : json({ status: "ok" }),
    );

    await uploadAvatar(
      new File([new Uint8Array([1])], "a.png", { type: "image/png" }),
    );
    await revokeOtherSessions();

    // The upload used to read the body and drop the headers, so the next write
    // went out on whatever token login handed over — stale after any rotation.
    expect(calls[1].headers["X-CSRF-Token"]).toBe("tok-rotated");
  });

  it("replays a CSRF-rejected upload once, on the token the 403 handed back", async () => {
    let attempts = 0;
    const calls = recordFetch((url) => {
      if (!url.endsWith("/avatar")) return json({ status: "ok" });
      attempts += 1;
      return attempts === 1
        ? json({ error: { code: "CSRF_FAILED", message: "无效令牌" } }, 403, {
            "X-CSRF-Token": "tok-reissued",
          })
        : json({ ...backendUser, avatar_url: null });
    });

    await uploadAvatar(
      new File([new Uint8Array([1])], "a.png", { type: "image/png" }),
    );

    // Without the replay this one raw-bytes write hard-fails on a stale token
    // while every `api.*` write self-heals — "everything works except my picture".
    expect(calls).toHaveLength(2);
    expect(calls[0].headers["X-CSRF-Token"]).toBeUndefined();
    expect(calls[1].headers["X-CSRF-Token"]).toBe("tok-reissued");
  });

  it("does not replay a 403 the server declined to re-arm", async () => {
    const calls = recordFetch(() =>
      json({ error: { code: "CSRF_FAILED", message: "无效令牌" } }, 403),
    );

    await expect(
      uploadAvatar(
        new File([new Uint8Array([1])], "a.png", { type: "image/png" }),
      ),
    ).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
  });
});

describe("logout", () => {
  /** Reply 403 CSRF_FAILED to the logout, 200 (+ token) to everything else. */
  const refuseLogout: Handler = (url) =>
    url.endsWith("/v1/auth/logout")
      ? json(
          {
            error: {
              code: "CSRF_FAILED",
              message: "CSRF token missing or invalid. Re-login and retry.",
            },
          },
          403,
        )
      : json({ status: "ok" }, 200, { "X-CSRF-Token": "tok-live" });

  it("wipes local session state even when the server refuses the logout", async () => {
    const calls = recordFetch(refuseLogout);
    await revokeOtherSessions(); // captures tok-live

    await expect(logout()).resolves.toBeUndefined();

    // A mutating call after logout must no longer carry the dead session's token:
    // proof the local wipe ran instead of being skipped by the thrown 403.
    await revokeOtherSessions();
    const afterLogout = calls[calls.length - 1];
    expect(afterLogout.url).toContain("/v1/auth/sessions/revoke-others");
    expect(afterLogout.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("still clears local state on a transport failure", async () => {
    const calls = recordFetch((url) => {
      if (url.endsWith("/v1/auth/logout")) throw new TypeError("offline");
      return json({ status: "ok" }, 200, { "X-CSRF-Token": "tok-live" });
    });
    await revokeOtherSessions();

    await expect(logout()).resolves.toBeUndefined();

    await revokeOtherSessions();
    expect(calls[calls.length - 1].headers["X-CSRF-Token"]).toBeUndefined();
  });
});

describe("deleteAvatar", () => {
  it("DELETEs the avatar and clears the mapped URL", async () => {
    const calls = captureFetch(json({ ...backendUser, avatar_url: null }));

    const user = await deleteAvatar();

    expect(calls[0].url).toContain("/v1/users/me/avatar");
    expect(calls[0].method).toBe("DELETE");
    expect(user.avatarUrl).toBeNull();
  });
});

describe("email register / reset / catch-up", () => {
  it("POSTs register send-code and returns expires_in", async () => {
    const calls = captureFetch(json({ status: "accepted", expires_in: 900 }));
    const result = await sendRegisterCode({
      password: "password1",
      email: "alice@example.com",
    });
    expect(calls[0].url).toContain("/v1/auth/register/send-code");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({
      password: "password1",
      email: "alice@example.com",
    });
    expect(result.expiresIn).toBe(900);
  });

  it("falls back when register send-code omits expires_in", async () => {
    captureFetch(new Response(null, { status: 202 }));
    const result = await sendRegisterCode({
      password: "password1",
      email: "alice@example.com",
    });
    expect(result.expiresIn).toBe(600);
  });

  it("POSTs register verify and maps email_verified_at", async () => {
    const calls = captureFetch(
      json({
        ...backendUser,
        email: "alice@example.com",
        email_verified_at: "2026-08-19T00:00:00Z",
      }),
    );
    const user = await verifyRegister("alice@example.com", "123456");
    expect(calls[0].url).toContain("/v1/auth/register/verify");
    expect(calls[0].body).toEqual({
      email: "alice@example.com",
      code: "123456",
    });
    expect(user.email).toBe("alice@example.com");
    expect(user.emailVerifiedAt).toBe("2026-08-19T00:00:00Z");
  });

  it("POSTs register verify with display_name when nickname is provided", async () => {
    const calls = captureFetch(
      json({
        ...backendUser,
        email: "alice@example.com",
        display_name: "小艾",
        email_verified_at: "2026-08-19T00:00:00Z",
      }),
    );
    await verifyRegister("alice@example.com", "123456", "小艾");
    expect(calls[0].body).toEqual({
      email: "alice@example.com",
      code: "123456",
      display_name: "小艾",
    });
  });

  it("POSTs password forgot / reset", async () => {
    const forgot = captureFetch(new Response(null, { status: 202 }));
    await forgotPassword("alice@example.com");
    expect(forgot[0].url).toContain("/v1/auth/password/forgot");
    expect(forgot[0].body).toEqual({ email: "alice@example.com" });

    const reset = captureFetch(json({ status: "ok" }));
    await resetPassword("alice@example.com", "123456", "newpass12");
    expect(reset[0].url).toContain("/v1/auth/password/reset");
    expect(reset[0].body).toEqual({
      email: "alice@example.com",
      code: "123456",
      new_password: "newpass12",
    });
  });

  it("POSTs logged-in email send-code / verify", async () => {
    const send = captureFetch(new Response(null, { status: 202 }));
    await sendEmailCode("alice@example.com");
    expect(send[0].url).toContain("/v1/auth/email/send-code");
    expect(send[0].body).toEqual({ email: "alice@example.com" });

    const verify = captureFetch(
      json({
        ...backendUser,
        email: "alice@example.com",
        email_verified_at: "2026-08-19T00:00:00Z",
      }),
    );
    const user = await verifyEmail("alice@example.com", "654321");
    expect(verify[0].url).toContain("/v1/auth/email/verify");
    expect(verify[0].body).toEqual({
      email: "alice@example.com",
      code: "654321",
    });
    expect(user.emailVerifiedAt).toBe("2026-08-19T00:00:00Z");
  });

  it("maps a missing email_verified_at to null", async () => {
    captureFetch(json({ ...backendUser, email: "legacy@example.com" }));
    const user = await fetchMe();
    expect(user.emailVerifiedAt).toBeNull();
  });
});

describe("listSessions / revokeSession / revokeOtherSessions", () => {
  const session = {
    id: "fam-1",
    platform: "desktop",
    user_agent: "Mozilla/5.0",
    ip: "127.0.0.1",
    created_at: "2026-07-01T00:00:00Z",
    last_used_at: "2026-07-12T00:00:00Z",
    current: true,
  };

  it("GETs /auth/sessions and returns the list payload", async () => {
    const calls = captureFetch(json({ data: [session], total: 1 }));

    const res = await listSessions();

    expect(calls[0].url).toContain("/v1/auth/sessions");
    expect(calls[0].method).toBeUndefined(); // GET default
    expect(res.total).toBe(1);
    expect(res.data[0].id).toBe("fam-1");
  });

  it("DELETEs /auth/sessions/{family_id}", async () => {
    const calls = captureFetch(json({ status: "ok" }));

    await revokeSession("fam-1");

    expect(calls[0].url).toContain("/v1/auth/sessions/fam-1");
    expect(calls[0].method).toBe("DELETE");
  });

  it("POSTs /auth/sessions/revoke-others", async () => {
    const calls = captureFetch(json({ status: "ok" }));

    await revokeOtherSessions();

    expect(calls[0].url).toContain("/v1/auth/sessions/revoke-others");
    expect(calls[0].method).toBe("POST");
  });
});
