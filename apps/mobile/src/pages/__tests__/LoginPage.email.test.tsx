// @vitest-environment jsdom
import {
  AuthApiError,
  forgotPassword,
  login,
  resetPassword,
  sendRegisterCode,
  verifyRegister,
} from "@/api/auth";
import { LoginPage } from "@/pages/LoginPage";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/auth", () => {
  class AuthApiError extends Error {
    constructor(
      readonly status: number,
      message: string,
      readonly code?: string,
    ) {
      super(message);
      this.name = "AuthApiError";
    }
  }
  return {
    login: vi.fn(),
    sendRegisterCode: vi.fn(),
    verifyRegister: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
    AuthApiError,
  };
});

vi.mock("@/lib/rememberedUsername", () => ({
  getRememberedUsername: () => null,
  setRememberedUsername: vi.fn(),
}));

function renderLogin() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

function fillRegisterCredentials() {
  fireEvent.click(screen.getByRole("tab", { name: "注册" }));
  fireEvent.change(screen.getByPlaceholderText("邮箱"), {
    target: { value: "alice@example.com" },
  });
  fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
    target: { value: "password1" },
  });
}

function fillRegisterForm() {
  fillRegisterCredentials();
  for (const box of screen.getAllByRole("checkbox")) {
    fireEvent.click(box);
  }
}

async function requestRegisterCode() {
  fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
  await waitFor(() => {
    expect(sendRegisterCode).toHaveBeenCalled();
  });
  await screen.findByRole("button", { name: /重新发送/ });
}

function fillForgotEmail() {
  fireEvent.click(screen.getByRole("button", { name: "忘记密码？" }));
  fireEvent.change(screen.getByPlaceholderText("邮箱"), {
    target: { value: "alice@example.com" },
  });
}

afterEach(cleanup);

describe("LoginPage · single-form register", () => {
  beforeEach(() => {
    vi.mocked(login).mockReset();
    vi.mocked(sendRegisterCode).mockReset();
    vi.mocked(verifyRegister).mockReset();
    vi.mocked(sendRegisterCode).mockResolvedValue({ expiresIn: 600 });
    vi.mocked(verifyRegister).mockResolvedValue({
      id: "u3",
      username: "alice",
      display_name: "Alice",
      email: "alice@example.com",
      email_verified_at: "2026-08-19T00:00:00Z",
      role: "user",
      created_at: "2026-08-19T00:00:00Z",
      password_must_change: false,
    });
    vi.mocked(login).mockResolvedValue({
      id: "u3",
      username: "alice",
      display_name: "Alice",
      email: "alice@example.com",
      email_verified_at: "2026-08-19T00:00:00Z",
      role: "user",
      created_at: "2026-08-19T00:00:00Z",
      password_must_change: false,
    });
  });

  it("sends a code, verifies, then logs in", async () => {
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();

    expect(sendRegisterCode).toHaveBeenCalledWith({
      password: "password1",
      email: "alice@example.com",
    });

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "12345" },
    });
    expect(verifyRegister).not.toHaveBeenCalled();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    expect(verifyRegister).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    await waitFor(() => {
      expect(verifyRegister).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        undefined,
      );
      expect(login).toHaveBeenCalledWith("alice@example.com", "password1");
    });
  });

  it("passes a non-empty nickname as display_name on verify", async () => {
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.change(screen.getByPlaceholderText("昵称（选填）"), {
      target: { value: "  Alice  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    await waitFor(() => {
      expect(verifyRegister).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        "Alice",
      );
    });
  });

  it("does not block send-code when nickname is empty", async () => {
    renderLogin();
    fillRegisterCredentials();
    await requestRegisterCode();
    expect(sendRegisterCode).toHaveBeenCalledWith({
      password: "password1",
      email: "alice@example.com",
    });
  });

  it("shows the server TTL after send-code succeeds", async () => {
    vi.mocked(sendRegisterCode).mockResolvedValue({ expiresIn: 900 });
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();
    expect(screen.getByText("已发送验证码，15 分钟内有效")).toBeTruthy();
    expect(screen.queryByText("验证码 15 分钟内有效")).toBeNull();
    expect(screen.queryByText(/已发送至/)).toBeNull();
  });

  it("stays on the same form when the code is wrong", async () => {
    vi.mocked(verifyRegister).mockRejectedValue(
      new Error("验证码错误或已过期"),
    );
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "000000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    expect(await screen.findByText("验证码错误或已过期")).toBeTruthy();
    expect(login).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "注册" })).toBeTruthy();
    expect(screen.getByPlaceholderText("验证码（6 位）")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "完成注册" })).toBeNull();
  });

  it("disables resend until the 60s cooldown elapses", async () => {
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();

    const resend = screen.getByRole("button", { name: /重新发送/ });
    expect(resend).toHaveProperty("disabled", true);
    expect(resend.textContent).toMatch(/重新发送（\d+s）/);
  });

  it("keeps the code field when send-code is rate-limited", async () => {
    vi.mocked(sendRegisterCode).mockRejectedValue(
      new Error("发送过于频繁，请稍后再试"),
    );
    renderLogin();
    fillRegisterForm();
    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));

    expect(await screen.findByText("发送过于频繁，请稍后再试")).toBeTruthy();
    expect(screen.getByPlaceholderText("验证码（6 位）")).toBeTruthy();
    expect(screen.getByRole("button", { name: "获取验证码" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "注册" })).toBeTruthy();
    expect(screen.queryByText(/步骤\s*[12]/)).toBeNull();
  });

  it("sends a code even when consent boxes are unchecked", async () => {
    renderLogin();
    fillRegisterCredentials();
    await requestRegisterCode();
    expect(screen.queryByText("请确认已年满 18 周岁")).toBeNull();
    expect(screen.queryByText("请同意用户协议和隐私政策")).toBeNull();
  });

  it("blocks register—not send-code—when consent is missing", async () => {
    renderLogin();
    fillRegisterCredentials();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();
    expect(screen.getByText("请确认已年满 18 周岁")).toBeTruthy();
    expect(screen.getByText("请同意用户协议和隐私政策")).toBeTruthy();
  });

  it("clears the code and requires a resend after the email changes", async () => {
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });

    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "bob@example.com" },
    });
    expect(screen.getByPlaceholderText("验证码（6 位）")).toHaveProperty(
      "value",
      "",
    );

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();
    expect(screen.getByText("请先获取验证码")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
    await waitFor(() => {
      expect(sendRegisterCode).toHaveBeenCalledTimes(2);
    });
    expect(sendRegisterCode).toHaveBeenLastCalledWith({
      password: "password1",
      email: "bob@example.com",
    });
  });

  it("clears the code and requires a resend after the password changes", async () => {
    renderLogin();
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });

    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "password2" },
    });
    expect(screen.getByPlaceholderText("验证码（6 位）")).toHaveProperty(
      "value",
      "",
    );

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
    await waitFor(() => {
      expect(sendRegisterCode).toHaveBeenCalledTimes(2);
    });
    expect(sendRegisterCode).toHaveBeenLastCalledWith({
      password: "password2",
      email: "alice@example.com",
    });
  });
});

describe("LoginPage · forgot password", () => {
  beforeEach(() => {
    vi.mocked(forgotPassword).mockReset();
    vi.mocked(resetPassword).mockReset();
    vi.mocked(forgotPassword).mockResolvedValue(undefined);
    vi.mocked(resetPassword).mockResolvedValue(undefined);
  });

  it("resets the password and returns to login", async () => {
    renderLogin();
    fillForgotEmail();
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await screen.findByPlaceholderText("验证码（6 位）");
    expect(screen.getByText("如果该邮箱已注册，你会收到验证码")).toBeTruthy();
    expect(screen.queryByText(/已发送至/)).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.change(screen.getByPlaceholderText("新密码（至少 8 位）"), {
      target: { value: "newpass12" },
    });
    fireEvent.change(screen.getByPlaceholderText("确认新密码"), {
      target: { value: "newpass12" },
    });
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    await waitFor(() => {
      expect(resetPassword).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        "newpass12",
      );
    });
    expect(screen.getByText("密码已重置，请使用新密码登录")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
  });

  it("stays on the reset step when the code is wrong", async () => {
    vi.mocked(resetPassword).mockRejectedValue(new Error("验证码错误或已过期"));
    renderLogin();
    fillForgotEmail();
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await screen.findByPlaceholderText("验证码（6 位）");

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "000000" },
    });
    fireEvent.change(screen.getByPlaceholderText("新密码（至少 8 位）"), {
      target: { value: "newpass12" },
    });
    fireEvent.change(screen.getByPlaceholderText("确认新密码"), {
      target: { value: "newpass12" },
    });
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    expect(await screen.findByText("验证码错误或已过期")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重置密码" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "登录" })).toBeNull();
  });

  it("disables resend until the 60s cooldown elapses", async () => {
    renderLogin();
    fillForgotEmail();
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await screen.findByPlaceholderText("验证码（6 位）");

    const resend = screen.getByRole("button", { name: /重新发送/ });
    expect(resend).toHaveProperty("disabled", true);
    expect(resend.textContent).toMatch(/重新发送（\d+s）/);
  });

  it("keeps the email form when send-code is rate-limited", async () => {
    vi.mocked(forgotPassword).mockRejectedValue(
      new Error("发送过于频繁，请稍后再试"),
    );
    renderLogin();
    fillForgotEmail();
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));

    expect(await screen.findByText("发送过于频繁，请稍后再试")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("验证码（6 位）")).toBeNull();
  });
});

describe("LoginPage · EMAIL_NOT_VERIFIED", () => {
  beforeEach(() => {
    vi.mocked(login).mockReset();
  });

  it("distinguishes unverified email from a wrong password and stays on login", async () => {
    vi.mocked(login).mockRejectedValue(
      new AuthApiError(403, "请先验证邮箱", "EMAIL_NOT_VERIFIED"),
    );
    renderLogin();
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("请先验证邮箱")).toBeTruthy();
    expect(screen.getByText("可在账户设置中补发验证码完成验证。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
    expect(screen.queryByText("用户名或密码错误")).toBeNull();
  });

  it("still shows the resend hint when EMAIL_NOT_VERIFIED arrives with a different message", async () => {
    vi.mocked(login).mockRejectedValue(
      new AuthApiError(403, "请验证你的电子邮箱", "EMAIL_NOT_VERIFIED"),
    );
    renderLogin();
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(
      await screen.findByText("可在账户设置中补发验证码完成验证。"),
    ).toBeTruthy();
    expect(screen.queryByText("请先验证邮箱")).toBeNull();
  });
});
