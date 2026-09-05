// @vitest-environment jsdom
/**
 * Tests for 设置·关于 — 品牌区 / 版本溯源 / 法律入口。软件更新分支见
 * AboutSettings.update.test.tsx。
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/capabilities")>()),
  hasAutoUpdater: vi.fn(() => true),
  hasLocalEngine: vi.fn(() => true),
  isWebRuntime: vi.fn(() => false),
}));
vi.mock("@/lib/clientBuildInfo", () => ({
  clientVersion: vi.fn(() => "0.6.1"),
  clientGitSha: vi.fn(() => "abcdef1"),
  formatGitSha: (sha: string) => sha,
}));
vi.mock("@/services/system", () => ({
  fetchVersion: vi.fn(() =>
    Promise.resolve({
      version: "1.0.0",
      gitSha: "deadbeef",
      builtAt: "2026-01-01T00:00:00Z",
    }),
  ),
}));

import { fetchVersion } from "@/services/system";
import { AboutSettings } from "../AboutSettings";

function renderPage() {
  return render(
    <MemoryRouter>
      <AboutSettings />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
});

describe("AboutSettings", () => {
  it("keeps a single page title — the brand block no longer competes with it", () => {
    const { container } = renderPage();
    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);
    expect(headings[0]?.textContent).toBe("关于 AgentCore");
    // 定位语留下了，只是降级成说明卡。
    expect(screen.getByText("协作，是更高级的智能。")).toBeTruthy();
  });

  it("lists client and API provenance as label/value rows", async () => {
    renderPage();
    expect(screen.getByText("客户端版本")).toBeTruthy();
    expect(screen.getByText("0.6.1")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("API 构建时间")).toBeTruthy();
    });
    expect(screen.getByText("2026-01-01T00:00:00Z")).toBeTruthy();
    expect(screen.getByText("deadbeef")).toBeTruthy();
  });

  it("keeps a failed version fetch distinct from an empty table", async () => {
    vi.mocked(fetchVersion).mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("获取版本信息失败")).toBeTruthy();
    });
    // 客户端侧信息不依赖后端，报障时仍读得到。
    expect(screen.getByText("客户端版本")).toBeTruthy();
    expect(screen.queryByText("API 版本")).toBeNull();
  });

  it("links to the legal documents", () => {
    renderPage();
    expect(
      screen.getByRole("link", { name: "用户协议" }).getAttribute("href"),
    ).toBe("/more/legal/terms");
    expect(
      screen.getByRole("link", { name: "隐私政策" }).getAttribute("href"),
    ).toBe("/more/legal/privacy");
  });

  it("does not host 允许本机执行 (that switch lives under 设置·通用·进阶)", () => {
    renderPage();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByText("开发者 / 诊断模式")).toBeNull();
    expect(screen.queryByText("允许本机执行")).toBeNull();
  });
});
