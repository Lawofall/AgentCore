// @vitest-environment jsdom
import { sendEmailCode, updateProfile } from "@/api/account";
import { me } from "@/api/auth";
import { getTokens } from "@/api/client";
import { AccountSettings } from "@/pages/more/AccountSettings";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/auth", () => ({
  me: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/api/client", () => ({
  getTokens: vi.fn(),
}));

vi.mock("@/api/sessions", () => ({
  listSessions: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  revokeSession: vi.fn(),
  revokeOtherSessions: vi.fn(),
}));

vi.mock("@/api/account", () => ({
  AVATAR_MAX_BYTES: 5 * 1024 * 1024,
  updateProfile: vi.fn(),
  changePassword: vi.fn(),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  deleteAccount: vi.fn(),
  sendEmailCode: vi.fn(),
  verifyEmail: vi.fn(),
}));

vi.mock("@/pages/more/Avatar", () => ({
  Avatar: () => <div data-testid="avatar" />,
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

const VERIFIED = {
  id: "u1",
  username: "alice",
  display_name: "Alice",
  email: "alice@example.com",
  email_verified_at: "2026-08-19T00:00:00Z",
  avatar_url: null,
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
  password_must_change: false,
};

afterEach(cleanup);

beforeEach(() => {
  vi.mocked(me).mockReset();
  vi.mocked(getTokens).mockReset();
  vi.mocked(updateProfile).mockReset();
  vi.mocked(getTokens).mockReturnValue({
    access_token: "a",
    refresh_token: "r",
  });
});

describe("AccountSettings email change", () => {
  it("changing email clears verification and does not auto-send a code", async () => {
    vi.mocked(me).mockResolvedValue(VERIFIED);
    vi.mocked(updateProfile).mockResolvedValue({
      ...VERIFIED,
      email: "new@example.com",
      email_verified_at: null,
    });

    render(<AccountSettings />);
    expect(await screen.findByText(/邮箱 · 已验证/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发送验证码" })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(updateProfile).toHaveBeenCalledWith({
        display_name: "Alice",
        email: "new@example.com",
      });
    });
    expect(screen.getByText(/邮箱 · 未验证/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeTruthy();
    expect(sendEmailCode).not.toHaveBeenCalled();
  });
});
