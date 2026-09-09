// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginSessionsSection } from "../LoginSessionsSection";

const listSessions = vi.fn();
const revokeSession = vi.fn();
const revokeOtherSessions = vi.fn();
const logout = vi.fn();

vi.mock("@/services/auth", () => ({
  listSessions: (...args: unknown[]) => listSessions(...args),
  revokeSession: (...args: unknown[]) => revokeSession(...args),
  revokeOtherSessions: (...args: unknown[]) => revokeOtherSessions(...args),
  logout: (...args: unknown[]) => logout(...args),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: Object.assign(() => ({ user: null }), {
    getState: () => ({ setUnauthenticated: vi.fn() }),
  }),
}));

const current = {
  id: "fam-current",
  platform: "desktop",
  user_agent:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Electron/28.0.0",
  ip: "1.2.3.4",
  created_at: "2026-07-01T00:00:00Z",
  last_used_at: "2026-07-12T11:00:00Z",
  current: true,
};

const other = {
  id: "fam-other",
  platform: "mobile",
  user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
  ip: "5.6.7.8",
  created_at: "2026-07-02T00:00:00Z",
  last_used_at: "2026-07-11T11:00:00Z",
  current: false,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  listSessions.mockResolvedValue({ data: [current, other], total: 2 });
  revokeSession.mockResolvedValue(undefined);
  revokeOtherSessions.mockResolvedValue(undefined);
  logout.mockResolvedValue(undefined);
});

describe("LoginSessionsSection", () => {
  it("renders sessions and refreshes the list after revoking another device", async () => {
    listSessions
      .mockResolvedValueOnce({ data: [current, other], total: 2 })
      .mockResolvedValueOnce({ data: [current], total: 1 });

    render(<LoginSessionsSection />);

    expect(await screen.findByText("Windows 桌面端")).toBeTruthy();
    expect(screen.getByText("iPhone")).toBeTruthy();
    expect(screen.getByText("当前设备")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "退出其他所有设备" }),
    ).toBeTruthy();

    const revokeButtons = screen.getAllByRole("button", { name: "退出" });
    expect(revokeButtons).toHaveLength(2);
    // current row first in list → other is the second 退出
    const otherRevoke = revokeButtons[1];
    expect(otherRevoke).toBeTruthy();
    fireEvent.click(otherRevoke as HTMLElement);

    fireEvent.click(screen.getByRole("button", { name: "确认退出" }));

    await waitFor(() => {
      expect(revokeSession).toHaveBeenCalledWith("fam-other");
    });
    await waitFor(() => {
      expect(listSessions).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.queryByText("iPhone")).toBeNull();
    });
    expect(
      screen.queryByRole("button", { name: "退出其他所有设备" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /还有 \d+ 台/ })).toBeNull();
  });

  it("revokes other devices then refreshes", async () => {
    listSessions
      .mockResolvedValueOnce({ data: [current, other], total: 2 })
      .mockResolvedValueOnce({ data: [current], total: 1 });

    render(<LoginSessionsSection />);
    await screen.findByText("Windows 桌面端");

    fireEvent.click(screen.getByRole("button", { name: "退出其他所有设备" }));
    fireEvent.click(screen.getByRole("button", { name: "退出其他设备" }));

    await waitFor(() => {
      expect(revokeOtherSessions).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(listSessions).toHaveBeenCalledTimes(2);
    });
  });

  it("folds extra devices and expands on demand", async () => {
    const extras = [
      {
        id: "fam-ipad",
        platform: "mobile",
        user_agent: "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)",
        ip: "10.0.0.2",
        created_at: "2026-07-03T00:00:00Z",
        last_used_at: "2026-07-10T11:00:00Z",
        current: false,
      },
      {
        id: "fam-android",
        platform: "mobile",
        user_agent: "Mozilla/5.0 (Linux; Android 14; Pixel 8)",
        ip: "10.0.0.3",
        created_at: "2026-07-04T00:00:00Z",
        last_used_at: "2026-07-09T11:00:00Z",
        current: false,
      },
      {
        id: "fam-web",
        platform: "desktop",
        user_agent:
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        ip: "10.0.0.4",
        created_at: "2026-07-05T00:00:00Z",
        last_used_at: "2026-07-08T11:00:00Z",
        current: false,
      },
      {
        id: "fam-admin",
        platform: "admin",
        user_agent: "curl/8.0",
        ip: "10.0.0.5",
        created_at: "2026-07-06T00:00:00Z",
        last_used_at: "2026-07-07T11:00:00Z",
        current: false,
      },
      {
        id: "fam-linux",
        platform: "desktop",
        user_agent:
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Electron/28.0.0",
        ip: "10.0.0.6",
        created_at: "2026-07-07T00:00:00Z",
        last_used_at: "2026-07-06T11:00:00Z",
        current: false,
      },
    ];
    listSessions.mockResolvedValue({
      data: [current, other, ...extras],
      total: 7,
    });

    render(<LoginSessionsSection />);

    expect(await screen.findByText("Windows 桌面端")).toBeTruthy();
    expect(screen.getByText("当前设备")).toBeTruthy();
    expect(screen.getByText("iPhone")).toBeTruthy();
    expect(screen.getByText("iPad")).toBeTruthy();
    expect(screen.getByText("Android")).toBeTruthy();
    expect(screen.getByText("网页")).toBeTruthy();
    expect(screen.queryByText("管理后台")).toBeNull();
    expect(screen.queryByText("Linux 桌面端")).toBeNull();
    expect(screen.getAllByRole("button", { name: "退出" })).toHaveLength(5);

    fireEvent.click(screen.getByRole("button", { name: "还有 2 台" }));

    expect(screen.getByText("管理后台")).toBeTruthy();
    expect(screen.getByText("Linux 桌面端")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "退出" })).toHaveLength(7);

    fireEvent.click(screen.getByRole("button", { name: "收起" }));

    expect(screen.queryByText("管理后台")).toBeNull();
    expect(screen.getByRole("button", { name: "还有 2 台" })).toBeTruthy();
  });
});
