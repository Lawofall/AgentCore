// @vitest-environment jsdom
import {
  forgotPassword,
  login,
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

afterEach(cleanup);

describe("LoginPage · form error line", () => {
  beforeEach(() => {
    vi.mocked(login).mockReset();
    vi.mocked(forgotPassword).mockReset();
    vi.mocked(sendRegisterCode).mockReset();
    vi.mocked(verifyRegister).mockReset();
  });

  it("shows empty-password error only after login is attempted", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    expect(screen.queryByText("请输入密码")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(login).not.toHaveBeenCalled();
    expect(screen.getByText("请输入密码")).toBeTruthy();
  });

  it("replaces the admin-reset copy with a forgot-password flow", async () => {
    vi.mocked(forgotPassword).mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(screen.queryByText(/请联系管理员重置/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "忘记密码？" }));
    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await waitFor(() => {
      expect(forgotPassword).toHaveBeenCalledWith("alice@example.com");
    });
    expect(screen.getByPlaceholderText("验证码（6 位）")).toBeTruthy();
  });

  it("sends a register code without requiring consent checkboxes", async () => {
    vi.mocked(sendRegisterCode).mockResolvedValue({ expiresIn: 600 });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "alice@example.com" },
    });
    expect(screen.queryByText("请设置密码")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "password1" },
    });
    expect(screen.queryByText("请确认已年满 18 周岁")).toBeNull();
    expect(screen.queryByText("请同意用户协议和隐私政策")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
    await waitFor(() => {
      expect(sendRegisterCode).toHaveBeenCalledWith({
        password: "password1",
        email: "alice@example.com",
      });
    });
    expect(screen.queryByText("请确认已年满 18 周岁")).toBeNull();
    expect(screen.queryByText("请同意用户协议和隐私政策")).toBeNull();
  });

  it("shows consent errors only after register is attempted", async () => {
    vi.mocked(sendRegisterCode).mockResolvedValue({ expiresIn: 600 });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "password1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
    await waitFor(() => {
      expect(sendRegisterCode).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();
    expect(screen.getByText("请确认已年满 18 周岁")).toBeTruthy();
    expect(screen.getByText("请同意用户协议和隐私政策")).toBeTruthy();
  });

  it("register stays on one form and sends a code instead of creating the account", async () => {
    vi.mocked(sendRegisterCode).mockResolvedValue({ expiresIn: 600 });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(screen.queryByText(/步骤\s*[12]/)).toBeNull();
    expect(screen.getByPlaceholderText("验证码（6 位）")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "password1" },
    });
    for (const box of screen.getAllByRole("checkbox")) {
      fireEvent.click(box);
    }
    const sendBtn = screen.getByRole("button", { name: "获取验证码" });
    expect(sendBtn).toHaveProperty("type", "button");
    expect(screen.getByRole("button", { name: "注册" })).toHaveProperty(
      "type",
      "submit",
    );
    fireEvent.click(sendBtn);
    await waitFor(() => {
      expect(sendRegisterCode).toHaveBeenCalledWith({
        password: "password1",
        email: "alice@example.com",
      });
    });
    expect(verifyRegister).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "注册" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "完成注册" })).toBeNull();
    expect(screen.queryByRole("button", { name: "发送验证码" })).toBeNull();
    expect(screen.queryByText(/步骤\s*[12]/)).toBeNull();
  });

  it("login failure is a generic .error line, not a needs-you bar", async () => {
    vi.mocked(login).mockRejectedValue(new Error("用户名或密码错误"));
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("用户名或密码错误")).toBeTruthy();
    const line = screen.getByText("用户名或密码错误").closest(".error");
    expect(line?.classList.contains("error")).toBe(true);
    expect(line?.className).not.toMatch(/\b(bar|inline-actions|needs-you)\b/);
  });
});
