// @vitest-environment jsdom
/**
 * Tests for 设置·通用 — 外观、发送方式、系统通知、开发者模式。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/useNarrowLayout", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/useNarrowLayout")>();
  return {
    ...actual,
    useNarrowLayout: vi.fn(() => false),
  };
});

import { useNarrowLayout } from "@/lib/useNarrowLayout";
import { useUIStore } from "@/stores/ui";
import { GeneralSettings } from "../GeneralSettings";

beforeEach(() => {
  vi.mocked(useNarrowLayout).mockReturnValue(false);
  useUIStore.setState({
    theme: "light",
    developerMode: false,
    fontSize: "md",
    zoom: 1,
    sendOnEnter: true,
    osNotifications: true,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderSettings(): Promise<void> {
  render(<GeneralSettings />);
  await screen.findByRole("heading", { name: "通用" });
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

describe("GeneralSettings · 字号", () => {
  it("marks 默认 and writes the store on click", async () => {
    await renderSettings();
    const large = screen.getByRole("tab", { name: "大" });
    expect(
      screen.getByRole("tab", { name: "默认" }).getAttribute("aria-selected"),
    ).toBe("true");
    fireEvent.click(large);
    expect(useUIStore.getState().fontSize).toBe("lg");
  });
});

describe("GeneralSettings · 界面缩放", () => {
  it("steps up and resets from the percent control", async () => {
    await renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "放大界面" }));
    expect(useUIStore.getState().zoom).toBe(1.1);
    expect(
      screen.getByRole("button", { name: "恢复默认缩放" }).textContent,
    ).toBe("110%");
    fireEvent.click(screen.getByRole("button", { name: "恢复默认缩放" }));
    expect(useUIStore.getState().zoom).toBe(1);
  });
});

describe("GeneralSettings · 发送", () => {
  it("marks Enter 发送 and writes the store on click", async () => {
    await renderSettings();
    expect(
      screen
        .getByRole("tab", { name: "Enter 发送" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Enter 换行" }));
    expect(useUIStore.getState().sendOnEnter).toBe(false);
  });
});

describe("GeneralSettings · 系统通知", () => {
  it("starts on and writes the switch", async () => {
    await renderSettings();
    const toggle = screen.getByRole("switch", { name: "系统通知" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    expect(useUIStore.getState().osNotifications).toBe(false);
  });
});

describe("GeneralSettings · 窄屏", () => {
  it("keeps 字号 and 发送, hides theme / zoom / OS notify / developer mode / search", async () => {
    vi.mocked(useNarrowLayout).mockReturnValue(true);
    await renderSettings();
    expect(screen.getByRole("tab", { name: "默认" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Enter 发送" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "联网搜索" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^浅色/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "放大界面" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "系统通知" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "开发者模式" })).toBeNull();
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
