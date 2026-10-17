// @vitest-environment jsdom
import { ApiError } from "@/services/api";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "../LoginPage";
import { LoginLockNotice } from "../login/LoginLockNotice";

const login = vi.fn();

vi.mock("@/services/auth", () => ({
  login: (...args: unknown[]) => login(...args),
  sendRegisterCode: vi.fn(),
  verifyRegister: vi.fn(),
  forgotPassword: vi.fn(),
  resetPassword: vi.fn(),
}));

vi.mock("@/services/offlineCache", () => ({
  cacheShellMeta: vi.fn(),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: (sel: (s: { setAuthenticated: () => void }) => unknown) =>
    sel({ setAuthenticated: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

function submitLogin(identifier: string, password: string) {
  fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
    target: { value: identifier },
  });
  fireEvent.change(screen.getByPlaceholderText("密码"), {
    target: { value: password },
  });
  const form = screen.getByPlaceholderText("邮箱或用户名").closest("form");
  if (!form) throw new Error("expected login form");
  fireEvent.submit(form);
}

describe("LoginPage lockout", () => {
  it("counts down from the unlock instant and holds the login button", async () => {
    const now = Date.parse("2026-10-14T09:00:00Z");
    vi.spyOn(Date, "now").mockReturnValue(now);
    login.mockRejectedValue(
      new ApiError(
        401,
        JSON.stringify({
          error: {
            code: "AUTH_ERROR",
            message: "账户已临时锁定，请稍后再试",
            recovery_at: "2026-10-14T09:01:30Z",
          },
        }),
      ),
    );
    render(<LoginPage />);
    submitLogin("alice", "wrong");

    expect(
      await screen.findByText(
        "账户已临时锁定，请 1 分 30 秒后再试。忘记密码可重置，重置后即可登录",
      ),
    ).toBeTruthy();
    const held = screen.getByRole("button", {
      name: "01:30 后可登录",
    }) as HTMLButtonElement;
    expect(held.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "忘记密码？" })).toBeTruthy();

    fireEvent.submit(
      screen
        .getByPlaceholderText("邮箱或用户名")
        .closest("form") as HTMLFormElement,
    );
    await waitFor(() => {
      expect(login).toHaveBeenCalledTimes(1);
    });
  });

  it("lets a different account try while this one is still locked", async () => {
    const now = Date.parse("2026-10-14T09:00:00Z");
    vi.spyOn(Date, "now").mockReturnValue(now);
    login.mockRejectedValue(
      new ApiError(
        401,
        JSON.stringify({
          error: {
            code: "AUTH_ERROR",
            message: "账户已临时锁定，请稍后再试",
            recovery_at: "2026-10-14T09:15:00Z",
          },
        }),
      ),
    );
    render(<LoginPage />);
    submitLogin("alice", "wrong");
    expect(await screen.findByText(/请 15 分钟后再试/)).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "bob" },
    });
    expect(screen.queryByText(/临时锁定/)).toBeNull();
    expect(
      (screen.getByRole("button", { name: "登录" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);

    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    expect(screen.getByText(/请 15 分钟后再试/)).toBeTruthy();
  });

  it("says login is open again once the wait is over", () => {
    render(<LoginLockNotice left={0} hint="忘记密码可重置，重置后即可登录" />);
    expect(screen.getByRole("status").textContent).toContain("可以重新登录了");
  });
});
