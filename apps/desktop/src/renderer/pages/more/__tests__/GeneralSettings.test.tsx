// @vitest-environment jsdom
/**
 * Tests for 设置·通用 — 主题与联网搜索。
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/searchConnection", () => ({
  getSearchConnection: vi.fn(),
  selectSearchEngine: vi.fn(),
  connectCleverSee: vi.fn(),
  connectSearxng: vi.fn(),
  disconnectSearchEngine: vi.fn(),
}));

import {
  type SearchConnectionResponse,
  connectCleverSee,
  getSearchConnection,
  selectSearchEngine,
} from "@/services/searchConnection";
import { useUIStore } from "@/stores/ui";
import { GeneralSettings } from "../GeneralSettings";

function connection(
  patch: Partial<SearchConnectionResponse> = {},
): SearchConnectionResponse {
  return {
    selected: "platform",
    quota: {
      daily_used: 3,
      daily_limit: 40,
      monthly_used: 12,
      monthly_limit: 400,
    },
    cleversee: null,
    searxng: null,
    ...patch,
  };
}

beforeEach(() => {
  vi.mocked(getSearchConnection).mockResolvedValue(connection());
  useUIStore.setState({ theme: "light", developerMode: false });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderSettings(): Promise<void> {
  render(<GeneralSettings />);
  await screen.findByRole("heading", { name: "联网搜索" });
}

describe("GeneralSettings · 主题", () => {
  it("marks the active theme and switches on click", async () => {
    await renderSettings();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("通用");
    expect(screen.queryByRole("heading", { name: "进阶" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "允许本机执行" })).toBeNull();

    const light = screen.getByRole("button", { name: /^浅色/ });
    const dark = screen.getByRole("button", { name: /^深色/ });
    expect(light.getAttribute("aria-pressed")).toBe("true");
    expect(dark.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(dark);
    expect(useUIStore.getState().theme).toBe("dark");
  });

  it("tells the 跟随系统 row what it currently resolves to", async () => {
    await renderSettings();
    const system = screen.getByRole("button", { name: /^跟随系统/ });
    expect(system.textContent).toContain("当前解析为");
  });
});

describe("GeneralSettings · 开发者模式", () => {
  it("starts off and writes the switch", async () => {
    await renderSettings();
    const toggle = screen.getByRole("switch", { name: "开发者模式" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(toggle);
    expect(useUIStore.getState().developerMode).toBe(true);
  });
});

describe("GeneralSettings · 联网搜索", () => {
  it("shows the platform remainder and two empty slots", async () => {
    await renderSettings();
    const platform = screen.getByRole("button", { name: /^平台搜索/ });
    expect(platform.getAttribute("aria-pressed")).toBe("true");
    expect(platform.textContent).toContain("今日 3/40");
    expect(platform.textContent).toContain("本月 12/400");
    expect(screen.getByRole("button", { name: "连接开析" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "连接 SearXNG" })).toBeTruthy();
  });

  it("keeps a failed 开析 connect on the form", async () => {
    const { ApiError } = await import("@/services/api");
    vi.mocked(connectCleverSee).mockRejectedValue(
      new ApiError(
        422,
        JSON.stringify({
          error: { message: "API Key 不正确。到开析控制台核对或新建" },
        }),
      ),
    );
    await renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "连接开析" }));
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "bad-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "连接开析" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "API Key 不正确",
    );
    expect(connectCleverSee).toHaveBeenCalledWith("bad-key");
    expect(screen.getByLabelText("API Key")).toBeTruthy();
  });

  it("selects 开析 after a successful connect response", async () => {
    vi.mocked(getSearchConnection).mockResolvedValue(
      connection({
        selected: "platform",
        cleversee: {
          status: "connected",
          base_url: "https://cloud-iqs.aliyuncs.com",
          masked_key: "••••abcd",
          message: null,
        },
      }),
    );
    vi.mocked(selectSearchEngine).mockResolvedValue(
      connection({
        selected: "cleversee",
        cleversee: {
          status: "connected",
          base_url: "https://cloud-iqs.aliyuncs.com",
          masked_key: "••••abcd",
          message: null,
        },
      }),
    );
    await renderSettings();
    fireEvent.click(screen.getByRole("button", { name: /^开析/ }));
    await waitFor(() => {
      expect(selectSearchEngine).toHaveBeenCalledWith("cleversee");
    });
    expect(
      screen
        .getByRole("button", { name: /^开析/ })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("shows the reconnect reason on the 开析 row", async () => {
    vi.mocked(getSearchConnection).mockResolvedValue(
      connection({
        selected: "cleversee",
        cleversee: {
          status: "needs_reconnect",
          base_url: "https://cloud-iqs.aliyuncs.com",
          masked_key: "••••abcd",
          message: "开析账户欠费",
        },
      }),
    );
    await renderSettings();
    expect(screen.getByRole("button", { name: /^开析/ }).textContent).toContain(
      "需要重新连接 · 开析账户欠费",
    );
  });
});
