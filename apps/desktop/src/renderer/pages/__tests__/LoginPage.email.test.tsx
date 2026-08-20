// @vitest-environment jsdom
import { ApiError } from "@/services/api";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "../LoginPage";

const login = vi.fn();
const sendRegisterCode = vi.fn();
const verifyRegister = vi.fn();
const forgotPassword = vi.fn();
const resetPassword = vi.fn();
const setAuthenticated = vi.fn();

vi.mock("@/services/auth", () => ({
  login: (...args: unknown[]) => login(...args),
  sendRegisterCode: (...args: unknown[]) => sendRegisterCode(...args),
  verifyRegister: (...args: unknown[]) => verifyRegister(...args),
  forgotPassword: (...args: unknown[]) => forgotPassword(...args),
  resetPassword: (...args: unknown[]) => resetPassword(...args),
}));

vi.mock("@/services/agentTownSession", () => ({
  persistAgentTownSession: vi.fn(),
}));

vi.mock("@/services/offlineCache", () => ({
  cacheShellMeta: vi.fn(),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: (
    sel: (s: { setAuthenticated: typeof setAuthenticated }) => unknown,
  ) => sel({ setAuthenticated }),
}));

function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, JSON.stringify({ error: { code, message } }));
}

const USER = {
  id: "u3",
  username: "alice",
  displayName: "Alice",
  email: "alice@example.com",
  emailVerifiedAt: "2026-08-19T00:00:00Z",
  role: "user",
  avatarUrl: null,
};

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
}

function fillForgotEmail() {
  fireEvent.click(screen.getByRole("button", { name: "忘记密码？" }));
  fireEvent.change(screen.getByPlaceholderText("邮箱"), {
    target: { value: "alice@example.com" },
  });
}

afterEach(() => {
  cleanup();
});

describe("LoginPage · single-form register", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sendRegisterCode.mockResolvedValue({ expiresIn: 600 });
    verifyRegister.mockResolvedValue(USER);
    login.mockResolvedValue(USER);
  });

  it("sends a code, verifies, then logs in", async () => {
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();

    expect(sendRegisterCode).toHaveBeenCalledWith({
      password: "password1",
      email: "alice@example.com",
    });
    expect(screen.getByText("已发送验证码，10 分钟内有效")).toBeTruthy();
    expect(screen.queryByText("验证码 10 分钟内有效")).toBeNull();
    expect(screen.queryByText(/已发送至/)).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    await waitFor(() => {
      expect(verifyRegister).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        undefined,
      );
      expect(login).toHaveBeenCalledWith("alice@example.com", "password1");
      expect(setAuthenticated).toHaveBeenCalled();
    });
  });

  it("passes a non-empty nickname as display_name on verify", async () => {
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.change(screen.getByLabelText("昵称（选填）"), {
      target: { value: "小艾" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    await waitFor(() => {
      expect(verifyRegister).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        "小艾",
      );
    });
  });

  it("still registers when nickname is left empty", async () => {
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    await waitFor(() => {
      expect(verifyRegister).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        undefined,
      );
    });
  });

  it("shows the server TTL after send-code succeeds", async () => {
    sendRegisterCode.mockResolvedValue({ expiresIn: 900 });
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();
    expect(screen.getByText("已发送验证码，15 分钟内有效")).toBeTruthy();
    expect(screen.queryByText("验证码 15 分钟内有效")).toBeNull();
  });

  it("stays on the same form when the code is wrong", async () => {
    verifyRegister.mockRejectedValue(
      apiError(400, "INVALID", "验证码错误或已过期"),
    );
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "000000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    expect(await screen.findByText("验证码错误或已过期")).toBeTruthy();
    expect(login).not.toHaveBeenCalled();
    expect(setAuthenticated).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "注册" })).toBeTruthy();
  });

  it("disables resend until the 60s cooldown elapses", async () => {
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();

    const resend = screen.getByRole("button", { name: /重新发送/ });
    expect(resend).toHaveProperty("disabled", true);
    expect(resend.textContent).toMatch(/重新发送（\d+s）/);
  });

  it("keeps the code field when send-code is rate-limited", async () => {
    sendRegisterCode.mockRejectedValue(
      apiError(429, "RATE_LIMITED", "发送过于频繁，请稍后再试"),
    );
    render(<LoginPage />);
    fillRegisterForm();
    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));

    expect(await screen.findByText("发送过于频繁，请稍后再试")).toBeTruthy();
    expect(screen.getByPlaceholderText("验证码（6 位）")).toBeTruthy();
    expect(screen.getByRole("button", { name: "获取验证码" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "注册" })).toBeTruthy();
    expect(screen.queryByText(/步骤\s*[12]/)).toBeNull();
  });

  it("clears the code and requires a resend after email or password changes", async () => {
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });

    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "bob@example.com" },
    });
    expect(
      (screen.getByPlaceholderText("验证码（6 位）") as HTMLInputElement).value,
    ).toBe("");

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();
    expect(screen.getByText("请先获取验证码")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
    await waitFor(() => expect(sendRegisterCode).toHaveBeenCalledTimes(2));
    expect(sendRegisterCode).toHaveBeenLastCalledWith({
      email: "bob@example.com",
      password: "password1",
    });

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "654321" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "password2" },
    });
    expect(
      (screen.getByPlaceholderText("验证码（6 位）") as HTMLInputElement).value,
    ).toBe("");
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "654321" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
    await waitFor(() => expect(sendRegisterCode).toHaveBeenCalledTimes(3));
    expect(sendRegisterCode).toHaveBeenLastCalledWith({
      email: "bob@example.com",
      password: "password2",
    });
  });

  it("does not auto-submit when the code reaches 6 digits", async () => {
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    expect(verifyRegister).not.toHaveBeenCalled();
  });
});

describe("LoginPage · forgot password", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgotPassword.mockResolvedValue(undefined);
    resetPassword.mockResolvedValue(undefined);
  });

  it("resets the password and returns to login", async () => {
    render(<LoginPage />);
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
    expect(screen.getByPlaceholderText("密码")).toBeTruthy();
  });

  it("stays on the reset step when the code is wrong", async () => {
    resetPassword.mockRejectedValue(
      apiError(400, "INVALID", "验证码错误或已过期"),
    );
    render(<LoginPage />);
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
    render(<LoginPage />);
    fillForgotEmail();
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await screen.findByPlaceholderText("验证码（6 位）");

    const resend = screen.getByRole("button", { name: /重新发送/ });
    expect(resend).toHaveProperty("disabled", true);
    expect(resend.textContent).toMatch(/重新发送（\d+s）/);
  });

  it("keeps the email form when send-code is rate-limited", async () => {
    forgotPassword.mockRejectedValue(
      apiError(429, "RATE_LIMITED", "发送过于频繁，请稍后再试"),
    );
    render(<LoginPage />);
    fillForgotEmail();
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));

    expect(await screen.findByText("发送过于频繁，请稍后再试")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("验证码（6 位）")).toBeNull();
  });
});

describe("LoginPage · EMAIL_NOT_VERIFIED", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("distinguishes unverified email from a wrong password and stays on login", async () => {
    login.mockRejectedValue(
      apiError(403, "EMAIL_NOT_VERIFIED", "请先验证邮箱"),
    );
    render(<LoginPage />);
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码"), {
      target: { value: "secret" },
    });
    const loginForm = screen
      .getByPlaceholderText("邮箱或用户名")
      .closest("form");
    if (!loginForm) throw new Error("expected login form");
    fireEvent.submit(loginForm);

    expect(await screen.findByText("请先验证邮箱")).toBeTruthy();
    expect(screen.getByText("可在账户设置中补发验证码完成验证。")).toBeTruthy();
    expect(screen.getByPlaceholderText("密码")).toBeTruthy();
    expect(screen.queryByText("用户名或密码错误")).toBeNull();
  });

  it("maps 401 to the wrong-password copy", async () => {
    login.mockRejectedValue(
      new ApiError(401, JSON.stringify({ error: { code: "AUTH_ERROR" } })),
    );
    render(<LoginPage />);
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码"), {
      target: { value: "wrong" },
    });
    const loginForm = screen
      .getByPlaceholderText("邮箱或用户名")
      .closest("form");
    if (!loginForm) throw new Error("expected login form");
    fireEvent.submit(loginForm);

    expect(await screen.findByText("用户名或密码错误")).toBeTruthy();
    expect(screen.queryByText("请先验证邮箱")).toBeNull();
  });
});

describe("LoginPage · live field hints", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sendRegisterCode.mockResolvedValue({ expiresIn: 600 });
  });

  it("explains an incomplete identifier instead of staying silent", () => {
    render(<LoginPage />);
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "ab" },
    });
    expect(screen.getByText("用户名至少 3 个字符")).toBeTruthy();
  });

  it("explains register blockers per field as the user types", () => {
    render(<LoginPage />);
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(screen.queryByText(/步骤\s*[12]/)).toBeNull();
    expect(screen.getByPlaceholderText("验证码（6 位）")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "ab" },
    });
    expect(screen.getByText("请输入有效邮箱")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "short" },
    });
    expect(screen.getByText("密码至少 8 位")).toBeTruthy();
  });

  it("toggles password visibility", () => {
    render(<LoginPage />);
    const field = screen.getByPlaceholderText("密码") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "显示密码" }));
    expect(field.type).toBe("text");
  });

  it("marks success and error outcomes differently without using red", async () => {
    verifyRegister.mockRejectedValue(
      apiError(400, "INVALID", "验证码错误或已过期"),
    );
    render(<LoginPage />);
    fillRegisterForm();
    await requestRegisterCode();
    const sent = await screen.findByText("已发送验证码，10 分钟内有效");
    expect(sent.closest("p")?.className).toMatch(/text-success/);
    expect(screen.getByText("成功：")).toBeTruthy();
    expect(screen.queryByText("验证码 10 分钟内有效")).toBeNull();
    expect(screen.queryByText(/已发送至/)).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "000000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    const failed = await screen.findByText("验证码错误或已过期");
    expect(failed.closest("p")?.className).toMatch(/text-muted-foreground/);
    expect(failed.closest("p")?.className).not.toMatch(/text-destructive/);
    expect(screen.getByText("错误：")).toBeTruthy();
  });
});
