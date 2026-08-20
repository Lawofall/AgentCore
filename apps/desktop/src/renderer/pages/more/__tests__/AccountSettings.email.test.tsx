// @vitest-environment jsdom
import { sendEmailCode, updateProfile, verifyEmail } from "@/services/auth";
import { type AuthUser, useAuthStore } from "@/stores/auth";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountSettings } from "../AccountSettings";

vi.mock("../LoginSessionsSection", () => ({
  LoginSessionsSection: () => null,
}));

vi.mock("@/services/auth", () => ({
  changePassword: vi.fn(),
  deleteAccount: vi.fn(),
  deleteAvatar: vi.fn(),
  updateProfile: vi.fn(),
  uploadAvatar: vi.fn(),
  sendEmailCode: vi.fn(),
  verifyEmail: vi.fn(),
  listSessions: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  logout: vi.fn(),
  revokeSession: vi.fn(),
  revokeOtherSessions: vi.fn(),
}));

const UNVERIFIED: AuthUser = {
  id: "u1",
  username: "alice",
  displayName: "Alice",
  email: "alice@example.com",
  emailVerifiedAt: null,
  role: "user",
  avatarUrl: null,
};

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    user: UNVERIFIED,
    sessionVerified: true,
    reason: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AccountSettings email catch-up", () => {
  it("shows unverified status and can send + verify a code", async () => {
    vi.mocked(sendEmailCode).mockResolvedValue(undefined);
    vi.mocked(verifyEmail).mockResolvedValue({
      ...UNVERIFIED,
      emailVerifiedAt: "2026-08-19T00:00:00Z",
    });

    render(<AccountSettings />);
    expect(screen.getByText("未验证")).toBeTruthy();
    expect(
      screen.getByText("验证邮箱后可用于找回密码。未验证不影响登录。"),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    await waitFor(() => {
      expect(sendEmailCode).toHaveBeenCalledWith("alice@example.com");
    });

    fireEvent.change(screen.getByPlaceholderText("验证码（6 位）"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));

    await waitFor(() => {
      expect(verifyEmail).toHaveBeenCalledWith("alice@example.com", "123456");
    });
  });

  it("changing email clears verification and does not auto-send a code", async () => {
    vi.mocked(updateProfile).mockResolvedValue({
      ...UNVERIFIED,
      email: "new@example.com",
      emailVerifiedAt: null,
    });
    useAuthStore.setState({
      user: {
        ...UNVERIFIED,
        email: "alice@example.com",
        emailVerifiedAt: "2026-08-19T00:00:00Z",
      },
    });

    render(<AccountSettings />);
    expect(screen.getByText("已验证")).toBeTruthy();
    expect(screen.queryByText("发送验证码")).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(updateProfile).toHaveBeenCalledWith({
        email: "new@example.com",
      });
    });
    expect(screen.getByText("未验证")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeTruthy();
    expect(sendEmailCode).not.toHaveBeenCalled();
  });
});

describe("AccountSettings profile", () => {
  it("shows nickname before username and allows claiming a handle", async () => {
    useAuthStore.setState({
      user: {
        id: "u1",
        username: "user_a3f90d12",
        displayName: "user_a3f90d12",
        email: "alice@example.com",
        emailVerifiedAt: "2026-08-19T00:00:00Z",
        role: "user",
        avatarUrl: null,
      },
    });
    vi.mocked(updateProfile).mockResolvedValue({
      ...UNVERIFIED,
      username: "alice",
      displayName: "Alice",
      emailVerifiedAt: "2026-08-19T00:00:00Z",
    });

    render(<AccountSettings />);
    expect(screen.getByText("昵称")).toBeTruthy();
    expect(screen.getByText("用户名")).toBeTruthy();
    expect(screen.getByText(/系统分配的找人码/)).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("你的昵称"), {
      target: { value: "Alice" },
    });
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "alice" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(updateProfile).toHaveBeenCalledWith({
        displayName: "Alice",
        username: "alice",
      });
    });
  });

  it("shows the 14-day username cooldown hint for a claimed handle", () => {
    useAuthStore.setState({
      user: {
        id: "u1",
        username: "alice",
        displayName: "Alice",
        email: "alice@example.com",
        emailVerifiedAt: "2026-08-19T00:00:00Z",
        role: "user",
        avatarUrl: null,
      },
    });

    render(<AccountSettings />);
    expect(screen.getByText("用户名 14 天内只能改一次。")).toBeTruthy();
    expect(screen.queryByText(/系统分配的找人码/)).toBeNull();
  });
});
