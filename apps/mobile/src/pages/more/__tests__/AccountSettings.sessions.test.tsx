// @vitest-environment jsdom
/**
 * Render + interaction tests for mobile 账户设置 · 登录设备.
 *
 * Asserts the session list renders device labels / 本机 badge, that revoke flows
 * hit the auth sessions API with a confirm step, and that revoking the current
 * device signs out + navigates to /login.
 */

import { me } from "@/api/auth";
import { getTokens } from "@/api/client";
import {
  listSessions,
  revokeOtherSessions,
  revokeSession,
} from "@/api/sessions";
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
  listSessions: vi.fn(),
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

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const mockMe = vi.mocked(me);
const mockGetTokens = vi.mocked(getTokens);
const mockList = vi.mocked(listSessions);
const mockRevoke = vi.mocked(revokeSession);
const mockRevokeOthers = vi.mocked(revokeOtherSessions);

const USER = {
  id: "u1",
  username: "alice",
  display_name: "Alice",
  email: null,
  avatar_url: null,
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
};

const SESSIONS = {
  data: [
    {
      id: "fam-current",
      platform: "mobile",
      user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
      ip: "1.2.3.4",
      created_at: "2026-07-12T10:00:00.000Z",
      last_used_at: "2026-07-12T11:50:00.000Z",
      current: true,
    },
    {
      id: "fam-other",
      platform: "desktop",
      user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      ip: "5.6.7.8",
      created_at: "2026-07-11T10:00:00.000Z",
      last_used_at: "2026-07-12T08:00:00.000Z",
      current: false,
    },
  ],
  total: 2,
};

afterEach(cleanup);
beforeEach(() => {
  mockMe.mockReset();
  mockGetTokens.mockReset();
  mockList.mockReset();
  mockRevoke.mockReset();
  mockRevokeOthers.mockReset();
  mockNavigate.mockReset();
  mockMe.mockResolvedValue(USER as never);
  mockGetTokens.mockReturnValue({
    access_token: "a",
    refresh_token: "r",
  } as never);
  mockList.mockResolvedValue(SESSIONS);
  mockRevoke.mockResolvedValue(undefined);
  mockRevokeOthers.mockResolvedValue(undefined);
});

describe("AccountSettings · 登录设备", () => {
  it("loads sessions and renders device labels, IP, and 本机 badge", async () => {
    render(<AccountSettings />);

    await waitFor(() => expect(screen.getByText("iPhone")).toBeTruthy());
    expect(screen.getByText("Windows 桌面端")).toBeTruthy();
    expect(screen.getByText("本机")).toBeTruthy();
    expect(screen.getByText("IP 1.2.3.4")).toBeTruthy();
    expect(screen.getByText("退出其他所有设备")).toBeTruthy();
  });

  it("revokes another device after confirm and refreshes the list", async () => {
    mockList.mockResolvedValueOnce(SESSIONS).mockResolvedValueOnce({
      data: [SESSIONS.data[0]],
      total: 1,
    });

    render(<AccountSettings />);
    await waitFor(() =>
      expect(screen.getByText("Windows 桌面端")).toBeTruthy(),
    );

    const exitButtons = screen.getAllByRole("button", { name: "退出" });
    expect(exitButtons.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(exitButtons[1] as HTMLElement);

    await waitFor(() => expect(screen.getByText("确认退出")).toBeTruthy());
    fireEvent.click(screen.getByText("确认退出"));

    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith("fam-other"));
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it("signs out and navigates to /login when revoking the current device", async () => {
    const { logout } = await import("@/api/auth");
    const mockLogout = vi.mocked(logout);

    render(<AccountSettings />);
    await waitFor(() => expect(screen.getByText("iPhone")).toBeTruthy());

    const exitButtons = screen.getAllByRole("button", { name: "退出" });
    expect(exitButtons.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(exitButtons[0] as HTMLElement);
    await waitFor(() => expect(screen.getByText("确认退出")).toBeTruthy());
    fireEvent.click(screen.getByText("确认退出"));

    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith("fam-current"));
    await waitFor(() => expect(mockLogout).toHaveBeenCalled());
    expect(mockNavigate).toHaveBeenCalledWith("/login", { replace: true });
  });

  it("revokes other devices after confirm", async () => {
    mockList.mockResolvedValueOnce(SESSIONS).mockResolvedValueOnce({
      data: [SESSIONS.data[0]],
      total: 1,
    });

    render(<AccountSettings />);
    await waitFor(() =>
      expect(screen.getByText("退出其他所有设备")).toBeTruthy(),
    );

    fireEvent.click(screen.getByText("退出其他所有设备"));
    await waitFor(() =>
      expect(screen.getByText("确认退出其他设备")).toBeTruthy(),
    );
    fireEvent.click(screen.getByText("确认退出其他设备"));

    await waitFor(() => expect(mockRevokeOthers).toHaveBeenCalled());
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it("hides 退出其他所有设备 when only one session remains", async () => {
    mockList.mockResolvedValue({
      data: [SESSIONS.data[0]],
      total: 1,
    });

    render(<AccountSettings />);
    await waitFor(() => expect(screen.getByText("iPhone")).toBeTruthy());
    expect(screen.queryByText("退出其他所有设备")).toBeNull();
  });
});
