/**
 * Login/register must send X-Client-Platform — server fail-closes on /v1/auth/token.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/push", () => ({
  enablePush: vi.fn(),
  disablePush: vi.fn(),
}));

vi.mock("@/lib/clientBuildInfo", () => ({
  clientHeaders: () => ({
    "X-Client-Platform": "android",
    "X-Client-Version": "test",
  }),
}));

import { AuthApiError, login, sendRegisterCode, verifyRegister } from "../auth";

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonOk({
        access_token: "a",
        refresh_token: "r",
        user: { id: "u1", username: "jhr123" },
      }),
    ),
  );
});

describe("auth · X-Client-Platform", () => {
  it("login POST /v1/auth/token includes platform header", async () => {
    await login("jhr123", "secret");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/auth/token"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-Client-Platform": "android",
          "X-Client-Version": "test",
        }),
      }),
    );
  });

  it("login maps EMAIL_NOT_VERIFIED apart from a 401", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "EMAIL_NOT_VERIFIED", message: "请先验证邮箱" },
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "AUTH_ERROR", message: "bad password" },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      );

    await expect(login("alice", "secret")).rejects.toMatchObject({
      name: "AuthApiError",
      code: "EMAIL_NOT_VERIFIED",
      status: 403,
      message: "请先验证邮箱",
    });
    await expect(login("alice", "wrong")).rejects.toMatchObject({
      name: "AuthApiError",
      status: 401,
      message: "用户名或密码错误",
    });
  });

  it("keeps EMAIL_NOT_VERIFIED when the server message changes", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: {
            code: "EMAIL_NOT_VERIFIED",
            message: "please verify your mailbox",
          },
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    );

    const err = await login("alice", "secret").then(
      () => {
        throw new Error("expected login to reject");
      },
      (reason: unknown) => reason,
    );
    expect(err).toBeInstanceOf(AuthApiError);
    expect(err).toMatchObject({
      code: "EMAIL_NOT_VERIFIED",
      status: 403,
    });
  });

  it("register send-code includes platform header", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 202 }));
    await sendRegisterCode({
      password: "secret",
      email: "jhr123@example.com",
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/auth/register/send-code"),
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Client-Platform": "android",
        }),
      }),
    );
  });

  it("register verify omits display_name when nickname is blank", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonOk({
        id: "u1",
        username: "user_deadbeef",
        display_name: "user_deadbeef",
      }),
    );
    await verifyRegister("alice@example.com", "123456");
    const init = vi.mocked(fetch).mock.calls.at(-1)?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      email: "alice@example.com",
      code: "123456",
    });
  });

  it("register verify sends display_name when nickname is provided", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonOk({ id: "u1", username: "user_deadbeef", display_name: "Alice" }),
    );
    await verifyRegister("alice@example.com", "123456", "Alice");
    const init = vi.mocked(fetch).mock.calls.at(-1)?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      email: "alice@example.com",
      code: "123456",
      display_name: "Alice",
    });
  });
});
