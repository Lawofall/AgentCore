// @vitest-environment jsdom
/**
 * 设置 · 联网搜索 — 平台次数、DeepSeek 服务位。
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/searchConnection", () => ({
  getSearchConnection: vi.fn(),
  selectSearchEngine: vi.fn(),
  connectDeepSeekFromProvider: vi.fn(),
  disconnectDeepSeek: vi.fn(),
}));

import {
  type SearchConnectionResponse,
  connectDeepSeekFromProvider,
  getSearchConnection,
  selectSearchEngine,
} from "@/services/searchConnection";
import { SearchConnectionSettings } from "../SearchConnectionSettings";

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
    deepseek: null,
    official_deepseek_provider: null,
    ...patch,
  };
}

beforeEach(() => {
  vi.mocked(getSearchConnection).mockResolvedValue(connection());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPage(): void {
  render(
    <MemoryRouter>
      <SearchConnectionSettings />
    </MemoryRouter>,
  );
}

describe("SearchConnectionSettings", () => {
  it("shows the platform remainder and sends people without a service to 服务商", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: "联网搜索" }),
    ).toBeTruthy();
    expect(screen.getByRole("img", { name: "当前使用 平台搜索" })).toBeTruthy();
    expect(screen.getByText(/今日 3\/40/)).toBeTruthy();
    expect(screen.getByText(/本月 12\/400/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "选用平台搜索" })).toBeNull();
    expect(screen.queryByRole("button", { name: "连接 DeepSeek" })).toBeNull();
    expect(screen.queryByLabelText("API Key")).toBeNull();
    expect(screen.getByText("到设置 · 服务商连接官方 DeepSeek")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "去服务商" }).getAttribute("href"),
    ).toBe("/more/providers");
  });

  it("selects DeepSeek from the connected card", async () => {
    vi.mocked(getSearchConnection).mockResolvedValue(
      connection({
        selected: "platform",
        official_deepseek_provider: { label: "DeepSeek" },
        deepseek: {
          status: "connected",
          base_url: "https://api.deepseek.com",
          source: "provider",
          masked_key: null,
          message: null,
        },
      }),
    );
    vi.mocked(selectSearchEngine).mockResolvedValue(
      connection({
        selected: "deepseek",
        official_deepseek_provider: { label: "DeepSeek" },
        deepseek: {
          status: "connected",
          base_url: "https://api.deepseek.com",
          source: "provider",
          masked_key: null,
          message: null,
        },
      }),
    );
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "选用 DeepSeek" }),
    );
    await waitFor(() => {
      expect(selectSearchEngine).toHaveBeenCalledWith("deepseek");
    });
    expect(screen.getByRole("img", { name: "当前使用 DeepSeek" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "选用 DeepSeek" })).toBeNull();
    expect(screen.getByRole("button", { name: "选用平台搜索" })).toBeTruthy();
  });

  it("shows the reconnect reason and reconnects from the service key", async () => {
    vi.mocked(getSearchConnection).mockResolvedValue(
      connection({
        selected: "deepseek",
        official_deepseek_provider: { label: "DeepSeek" },
        deepseek: {
          status: "needs_reconnect",
          base_url: "https://api.deepseek.com",
          source: "provider",
          masked_key: null,
          message: "DeepSeek 账户余额不足",
        },
      }),
    );
    renderPage();
    expect(
      (await screen.findByText(/需要重新连接 · DeepSeek 账户余额不足/))
        .textContent,
    ).toContain("DeepSeek 账户余额不足");
    expect(screen.getByRole("img", { name: "当前使用 DeepSeek" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "重新连接 DeepSeek" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "选用 DeepSeek" })).toBeNull();
  });

  it("connects with the official service key", async () => {
    vi.mocked(getSearchConnection).mockResolvedValue(
      connection({
        official_deepseek_provider: { label: "DeepSeek" },
      }),
    );
    vi.mocked(connectDeepSeekFromProvider).mockResolvedValue(
      connection({
        selected: "deepseek",
        official_deepseek_provider: { label: "DeepSeek" },
        deepseek: {
          status: "connected",
          base_url: "https://api.deepseek.com",
          source: "provider",
          masked_key: null,
          message: null,
        },
      }),
    );
    renderPage();
    expect(
      await screen.findByText("用已连接的服务商「DeepSeek」的 Key"),
    ).toBeTruthy();
    expect(screen.queryByLabelText("API Key")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "连接 DeepSeek" }));
    await waitFor(() => {
      expect(connectDeepSeekFromProvider).toHaveBeenCalled();
    });
    expect(screen.getByText("来自已连接的服务商 · DeepSeek")).toBeTruthy();
    expect(screen.getByRole("img", { name: "当前使用 DeepSeek" })).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "更换 DeepSeek 密钥" }),
    ).toBeNull();
  });
});
