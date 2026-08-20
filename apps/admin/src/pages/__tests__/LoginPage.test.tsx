// @vitest-environment jsdom

import { LoginPage } from "@/pages/LoginPage";
import { loginMfa } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/auth", () => ({
  login: vi.fn(),
  loginMfa: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  useAuthStore.setState({
    status: "unauthenticated",
    user: null,
    pendingMfaToken: "pending-token",
    mfaSetupRequired: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LoginPage MFA", () => {
  it("caps TOTP input at 6 digits and enables submit only at length === 6", () => {
    render(<LoginPage />);

    const input = screen.getByPlaceholderText("验证码（6 位）") as HTMLInputElement;
    const submit = screen.getByRole("button", { name: "验证并登录" }) as HTMLButtonElement;

    expect(submit.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "12345" } });
    expect(input.value).toBe("12345");
    expect(submit.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "123456" } });
    expect(input.value).toBe("123456");
    expect(submit.disabled).toBe(false);

    fireEvent.change(input, { target: { value: "123456789" } });
    expect(input.value).toBe("123456");
    expect(submit.disabled).toBe(false);
  });

  it("accepts a 16-hex recovery code and sends it as recovery_code, not code", async () => {
    vi.mocked(loginMfa).mockResolvedValue({
      kind: "success",
      user: {
        id: "u1",
        username: "root",
        displayName: "Root",
        email: null,
        emailVerifiedAt: null,
        role: "admin",
        passwordMustChange: false,
      },
    });
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: /恢复码登录/ }));
    const input = screen.getByPlaceholderText("恢复码（16 位）") as HTMLInputElement;
    const submit = screen.getByRole("button", { name: "验证并登录" }) as HTMLButtonElement;

    // Written-down codes carry dashes/uppercase; both normalize away before submit.
    fireEvent.change(input, { target: { value: "A1B2-C3D4-E5F6-A7B8" } });
    expect(input.value).toBe("a1b2c3d4e5f6a7b8");
    expect(submit.disabled).toBe(false);

    fireEvent.click(submit);
    await waitFor(() =>
      expect(loginMfa).toHaveBeenCalledWith("pending-token", {
        recoveryCode: "a1b2c3d4e5f6a7b8",
      }),
    );
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("keeps submit disabled until a recovery code is complete", () => {
    render(<LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: /恢复码登录/ }));

    const input = screen.getByPlaceholderText("恢复码（16 位）") as HTMLInputElement;
    const submit = screen.getByRole("button", { name: "验证并登录" }) as HTMLButtonElement;

    fireEvent.change(input, { target: { value: "a1b2c3d4" } });
    expect(submit.disabled).toBe(true);

    // Non-hex characters are rejected outright rather than padding the length.
    fireEvent.change(input, { target: { value: "zzzz-zzzz-zzzz-zzzz" } });
    expect(input.value).toBe("");
    expect(submit.disabled).toBe(true);
  });

  it("clears the entered code when switching factor, so it is never sent to the wrong field", () => {
    render(<LoginPage />);

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: /恢复码登录/ }));
    expect(
      (screen.getByPlaceholderText("恢复码（16 位）") as HTMLInputElement).value,
    ).toBe("");

    fireEvent.click(screen.getByRole("button", { name: /改用验证器/ }));
    expect(
      (screen.getByPlaceholderText("验证码（6 位）") as HTMLInputElement).value,
    ).toBe("");
  });
});

describe("LoginPage credentials", () => {
  beforeEach(() => {
    useAuthStore.setState({
      status: "unauthenticated",
      user: null,
      pendingMfaToken: null,
      mfaSetupRequired: false,
    });
  });

  it("accepts email or username and explains a short identifier", () => {
    render(<LoginPage />);
    expect(screen.getByPlaceholderText("邮箱或用户名")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("邮箱或用户名"), {
      target: { value: "ab" },
    });
    expect(screen.getByText("用户名至少 3 个字符")).toBeTruthy();
  });

  it("toggles password visibility", () => {
    render(<LoginPage />);
    const field = screen.getByPlaceholderText("密码") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "显示密码" }));
    expect(field.type).toBe("text");
  });
});
