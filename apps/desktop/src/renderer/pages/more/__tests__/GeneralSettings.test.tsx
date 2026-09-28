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

vi.mock("@/services/searchProviders", () => ({
  getSearchProviders: vi.fn(),
  selectSearchProvider: vi.fn(),
  createSearchProvider: vi.fn(),
  deleteSearchProvider: vi.fn(),
  testSearchProvider: vi.fn(),
}));

import {
  type SearchProvidersResponse,
  getSearchProviders,
  selectSearchProvider,
} from "@/services/searchProviders";
import { useUIStore } from "@/stores/ui";
import { GeneralSettings } from "../GeneralSettings";

function providers(
  patch: Partial<SearchProvidersResponse> = {},
): SearchProvidersResponse {
  return {
    selected_provider_id: null,
    quota: {
      daily_used: 3,
      daily_limit: 40,
      monthly_used: 12,
      monthly_limit: 400,
    },
    providers: [
      {
        id: "p1",
        label: "开析",
        protocol: "cleversee",
        base_url: "https://cloud-iqs.aliyuncs.com",
        status: "unchecked",
        masked_key: "••••abcd",
      },
    ],
    ...patch,
  };
}

beforeEach(() => {
  vi.mocked(getSearchProviders).mockResolvedValue(providers());
  useUIStore.setState({ theme: "light" });
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

describe("GeneralSettings · 联网搜索", () => {
  it("shows the platform remainder and selects an own provider", async () => {
    vi.mocked(selectSearchProvider).mockResolvedValue(
      providers({ selected_provider_id: "p1" }),
    );
    await renderSettings();
    const platform = screen.getByRole("button", { name: /^平台搜索/ });
    expect(platform.getAttribute("aria-pressed")).toBe("true");
    expect(platform.textContent).toContain("今日 3/40");
    expect(platform.textContent).toContain("本月 12/400");

    fireEvent.click(screen.getByRole("button", { name: /^开析/ }));
    await waitFor(() => {
      expect(selectSearchProvider).toHaveBeenCalledWith("p1");
    });
    expect(
      screen
        .getByRole("button", { name: /^开析/ })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
