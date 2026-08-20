import {
  REMEMBERED_USERNAME_KEY,
  saveRememberedUsername,
} from "@/lib/rememberedUsername";
// @vitest-environment jsdom
import {
  __clearMemoryUiStorageForTests,
  __setUiStorageBackendForTests,
  uiGet,
} from "@/lib/uiStorage";
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
const cacheShellMeta = vi.fn();
const persistAgentTownSession = vi.fn();

vi.mock("@/services/auth", () => ({
  login: (...args: unknown[]) => login(...args),
  sendRegisterCode: (...args: unknown[]) => sendRegisterCode(...args),
  verifyRegister: (...args: unknown[]) => verifyRegister(...args),
  forgotPassword: (...args: unknown[]) => forgotPassword(...args),
  resetPassword: (...args: unknown[]) => resetPassword(...args),
}));

vi.mock("@/services/agentTownSession", () => ({
  persistAgentTownSession: (...args: unknown[]) =>
    persistAgentTownSession(...args),
}));

vi.mock("@/services/offlineCache", () => ({
  cacheShellMeta: (...args: unknown[]) => cacheShellMeta(...args),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: (
    sel: (s: { setAuthenticated: typeof setAuthenticated }) => unknown,
  ) => sel({ setAuthenticated }),
}));

const mem = new Map<string, string>();

afterEach(() => {
  cleanup();
  __setUiStorageBackendForTests(null);
  __clearMemoryUiStorageForTests();
});

describe("LoginPage legal gates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mem.clear();
    __setUiStorageBackendForTests({
      getItem: (k) => mem.get(k) ?? null,
      setItem: (k, v) => {
        mem.set(k, v);
      },
      removeItem: (k) => {
        mem.delete(k);
      },
      keys: () => [...mem.keys()],
    });
  });

  it("sends a register code without requiring age or agreement", async () => {
    sendRegisterCode.mockResolvedValue({ expiresIn: 600 });
    render(<LoginPage />);

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
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
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

  it("blocks register, not send-code, until age and agreement are checked", async () => {
    sendRegisterCode.mockResolvedValue({ expiresIn: 600 });
    verifyRegister.mockResolvedValue({
      id: "u3",
      username: "alice",
      displayName: "Alice",
      email: "alice@example.com",
      emailVerifiedAt: "2026-08-19T00:00:00Z",
      role: "user",
      avatarUrl: null,
    });
    login.mockResolvedValue({
      id: "u3",
      username: "alice",
      displayName: "Alice",
      email: "alice@example.com",
      emailVerifiedAt: "2026-08-19T00:00:00Z",
      role: "user",
      avatarUrl: null,
    });
    render(<LoginPage />);

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
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(verifyRegister).not.toHaveBeenCalled();
    expect(screen.getByText("请确认已年满 18 周岁")).toBeTruthy();
    expect(screen.getByText("请同意用户协议和隐私政策")).toBeTruthy();

    for (const box of screen.getAllByRole("checkbox")) {
      fireEvent.click(box);
    }
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() => {
      expect(verifyRegister).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
        undefined,
      );
    });
  });

  it("opens user agreement pane from register consent link", () => {
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fireEvent.click(screen.getByRole("button", { name: "《用户协议》" }));
    expect(screen.getByRole("heading", { name: "用户服务协议" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /返回/ }));
    expect(screen.getByPlaceholderText("邮箱")).toBeTruthy();
  });

  it("writes offline shell user via cacheShellMeta after password login", async () => {
    const user = {
      id: "u1",
      username: "alice",
      displayName: "Alice",
      email: null,
      emailVerifiedAt: null,
      role: "user",
      avatarUrl: null,
    };
    login.mockResolvedValue(user);

    render(<LoginPage />);
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码"), {
      target: { value: "secret" },
    });
    const form = screen.getByPlaceholderText("邮箱或用户名").closest("form");
    expect(form).not.toBeNull();
    if (!form) throw new Error("expected login form");
    fireEvent.submit(form);

    await waitFor(() => {
      expect(setAuthenticated).toHaveBeenCalledWith(user);
      expect(cacheShellMeta).toHaveBeenCalledWith({ user });
    });
  });

  it("shows empty-password error only after login is attempted", () => {
    saveRememberedUsername("bob");
    render(<LoginPage />);
    expect(screen.queryByText("请输入密码")).toBeNull();
    const form = screen.getByPlaceholderText("邮箱或用户名").closest("form");
    expect(form).not.toBeNull();
    if (!form) throw new Error("expected login form");
    fireEvent.submit(form);
    expect(login).not.toHaveBeenCalled();
    expect(screen.getByText("请输入密码")).toBeTruthy();
  });

  it("prefills remembered username and persists it after successful login", async () => {
    saveRememberedUsername("bob");
    const user = {
      id: "u2",
      username: "carol",
      displayName: "Carol",
      email: null,
      emailVerifiedAt: null,
      role: "user",
      avatarUrl: null,
    };
    login.mockResolvedValue(user);

    const { unmount } = render(<LoginPage />);
    const usernameInput = screen.getByPlaceholderText(
      "邮箱或用户名",
    ) as HTMLInputElement;
    expect(usernameInput.value).toBe("bob");
    const passwordInput = screen.getByPlaceholderText(
      "密码",
    ) as HTMLInputElement;
    expect(passwordInput.value).toBe("");
    expect(screen.queryByText("请输入密码")).toBeNull();

    fireEvent.change(usernameInput, { target: { value: "carol" } });
    fireEvent.change(passwordInput, { target: { value: "secret123" } });
    const form = usernameInput.closest("form");
    expect(form).not.toBeNull();
    if (!form) throw new Error("expected login form");
    fireEvent.submit(form);

    await waitFor(() => {
      expect(setAuthenticated).toHaveBeenCalledWith(user);
      expect(uiGet<string>(REMEMBERED_USERNAME_KEY)).toBe("carol");
    });

    // Stored payload is username only — no password residue in uiStorage values.
    for (const value of mem.values()) {
      expect(value).not.toContain("secret123");
    }

    unmount();
    render(<LoginPage />);
    expect(
      (screen.getByPlaceholderText("邮箱或用户名") as HTMLInputElement).value,
    ).toBe("carol");
    expect(
      (screen.getByPlaceholderText("密码") as HTMLInputElement).value,
    ).toBe("");
  });

  it("register is one form: get-code then verify then login", async () => {
    sendRegisterCode.mockResolvedValue({ expiresIn: 600 });
    verifyRegister.mockResolvedValue({
      id: "u3",
      username: "alice",
      displayName: "Alice",
      email: "alice@example.com",
      emailVerifiedAt: "2026-08-19T00:00:00Z",
      role: "user",
      avatarUrl: null,
    });
    login.mockResolvedValue({
      id: "u3",
      username: "alice",
      displayName: "Alice",
      email: "alice@example.com",
      emailVerifiedAt: "2026-08-19T00:00:00Z",
      role: "user",
      avatarUrl: null,
    });

    render(<LoginPage />);
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(screen.queryByText(/步骤\s*[12]/)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("密码（至少 8 位）"), {
      target: { value: "password1" },
    });
    for (const box of screen.getAllByRole("checkbox")) {
      fireEvent.click(box);
    }
    fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));

    await waitFor(() => {
      expect(sendRegisterCode).toHaveBeenCalledWith({
        password: "password1",
        email: "alice@example.com",
      });
    });

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

  it("forgot password replaces the admin-reset copy", async () => {
    forgotPassword.mockResolvedValue(undefined);
    resetPassword.mockResolvedValue(undefined);

    render(<LoginPage />);
    expect(screen.queryByText(/请联系管理员重置/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "忘记密码？" }));

    fireEvent.change(screen.getByPlaceholderText("邮箱"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));

    await waitFor(() => {
      expect(forgotPassword).toHaveBeenCalledWith("alice@example.com");
    });

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
  });

  it("keeps a username identifier on login and does not copy it into register email", () => {
    saveRememberedUsername("dave");
    render(<LoginPage />);

    expect(
      (screen.getByPlaceholderText("邮箱或用户名") as HTMLInputElement).value,
    ).toBe("dave");

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(
      (screen.getByPlaceholderText("邮箱") as HTMLInputElement).value,
    ).toBe("");
    expect(
      (screen.getByPlaceholderText("密码（至少 8 位）") as HTMLInputElement)
        .value,
    ).toBe("");
    expect(screen.queryByText(/保持登录|记住密码|记住我/)).toBeNull();
  });
});
