// @vitest-environment jsdom
import { updateProfile } from "@/api/account";
import { me } from "@/api/auth";
import { getTokens } from "@/api/client";
import { copyText } from "@/lib/messageExport";
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

vi.mock("@/lib/messageExport", () => ({
  copyText: vi.fn(),
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

const SYSTEM_USER = {
  id: "u1",
  username: "user_a3f90d12",
  display_name: "user_a3f90d12",
  email: "alice@example.com",
  email_verified_at: "2026-08-19T00:00:00Z",
  avatar_url: null,
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
  password_must_change: false,
};

const CLAIMED_USER = {
  ...SYSTEM_USER,
  username: "alice",
  display_name: "Alice",
};

afterEach(cleanup);

beforeEach(() => {
  vi.mocked(me).mockReset();
  vi.mocked(getTokens).mockReset();
  vi.mocked(updateProfile).mockReset();
  vi.mocked(copyText).mockReset();
  vi.mocked(getTokens).mockReturnValue({
    access_token: "a",
    refresh_token: "r",
  });
  vi.mocked(copyText).mockResolvedValue(true);
});

describe("AccountSettings profile", () => {
  it("shows nickname label and system-handle hints", async () => {
    vi.mocked(me).mockResolvedValue(SYSTEM_USER);
    render(<AccountSettings />);

    expect(await screen.findByText("昵称")).toBeTruthy();
    expect(screen.queryByText("显示名")).toBeNull();
    expect(
      screen.getByText(
        /这是系统分配的找人码。可改成你希望别人用来搜索你的用户名/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        /当前昵称是系统分配的找人码。改成你希望别人看到的名字即可/,
      ),
    ).toBeTruthy();
  });

  it("copies the saved username", async () => {
    vi.mocked(me).mockResolvedValue(CLAIMED_USER);
    render(<AccountSettings />);
    expect(await screen.findByDisplayValue("alice")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "复制用户名" }));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith("alice");
    });
    expect(screen.getByRole("button", { name: "复制用户名" }).textContent).toBe(
      "已复制",
    );
  });

  it("saves nickname and claimed username together", async () => {
    vi.mocked(me).mockResolvedValue(SYSTEM_USER);
    vi.mocked(updateProfile).mockResolvedValue({
      ...SYSTEM_USER,
      username: "bob",
      display_name: "Bob",
    });
    render(<AccountSettings />);
    expect(await screen.findByPlaceholderText("你的昵称")).toHaveProperty(
      "value",
      "user_a3f90d12",
    );

    fireEvent.change(screen.getByPlaceholderText("你的昵称"), {
      target: { value: "Bob" },
    });
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "bob" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(updateProfile).toHaveBeenCalledWith({
        display_name: "Bob",
        email: "alice@example.com",
        username: "bob",
      });
    });
  });

  it("shows the 14-day cooldown hint for a claimed username", async () => {
    vi.mocked(me).mockResolvedValue(CLAIMED_USER);
    render(<AccountSettings />);

    expect(
      await screen.findByText("自选用户名后 14 天内不能再次修改。"),
    ).toBeTruthy();
    expect(screen.queryByText(/这是系统分配的找人码/)).toBeNull();
  });
});
