// @vitest-environment jsdom
/**
 * Tests for 设置·通用 (原「外观」) — 主题 + 有本机引擎时的进阶「允许本机执行」。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/capabilities")>()),
  hasLocalEngine: vi.fn(() => true),
}));
vi.mock("@/services/sidecarHealth", () => ({ clearSidecarHealth: vi.fn() }));

import { hasLocalEngine } from "@/lib/capabilities";
import { clearSidecarHealth } from "@/services/sidecarHealth";
import { useUIStore } from "@/stores/ui";
import { GeneralSettings } from "../GeneralSettings";

beforeEach(() => {
  vi.mocked(hasLocalEngine).mockReturnValue(true);
  useUIStore.setState({
    theme: "light",
    sidecarPreference: "unset",
    sidecarEnabled: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GeneralSettings · 主题", () => {
  it("marks the active theme and switches on click", () => {
    render(<GeneralSettings />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("通用");

    const light = screen.getByRole("button", { name: /^浅色/ });
    const dark = screen.getByRole("button", { name: /^深色/ });
    expect(light.getAttribute("aria-pressed")).toBe("true");
    expect(dark.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(dark);
    expect(useUIStore.getState().theme).toBe("dark");
  });

  it("tells the 跟随系统 row what it currently resolves to", () => {
    render(<GeneralSettings />);
    const system = screen.getByRole("button", { name: /^跟随系统/ });
    expect(system.textContent).toContain("当前解析为");
  });
});

describe("GeneralSettings · 进阶开关（原在关于页）", () => {
  it("hosts 允许本机执行 on a local-engine build without a parent toggle", () => {
    render(<GeneralSettings />);
    expect(screen.getByRole("heading", { name: "进阶" })).toBeTruthy();
    expect(screen.getByRole("switch", { name: "允许本机执行" })).toBeTruthy();
    expect(
      screen.queryByRole("switch", { name: "开发者 / 诊断模式" }),
    ).toBeNull();
  });

  it("hides the whole 进阶 section without a local engine", () => {
    vi.mocked(hasLocalEngine).mockReturnValue(false);
    render(<GeneralSettings />);
    expect(screen.queryByRole("heading", { name: "进阶" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "允许本机执行" })).toBeNull();
  });

  it("reads 允许本机执行 from the preference, not from sidecarEnabled", () => {
    // unset + sidecarEnabled=false 仍是「允许」——路由默认走同侧引擎。
    render(<GeneralSettings />);
    expect(
      screen
        .getByRole("switch", { name: "允许本机执行" })
        .getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("clears cached sidecar health when re-allowing local execution", () => {
    useUIStore.setState({ sidecarPreference: "off" });
    render(<GeneralSettings />);
    const toggle = screen.getByRole("switch", { name: "允许本机执行" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(toggle);
    expect(useUIStore.getState().sidecarPreference).toBe("on");
    expect(clearSidecarHealth).toHaveBeenCalledTimes(1);
  });
});
