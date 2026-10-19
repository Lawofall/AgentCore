import { describe, expect, it } from "vitest";
import {
  isNarrowBlockedPath,
  isNarrowHiddenPaletteId,
  narrowBlockedRedirect,
} from "../narrowProduct";

describe("isNarrowBlockedPath", () => {
  it("blocks toolbox / hidden settings", () => {
    expect(isNarrowBlockedPath("/toolbox")).toBe(true);
    expect(isNarrowBlockedPath("/toolbox/manual/intro")).toBe(true);
    expect(isNarrowBlockedPath("/whiteboard/abc")).toBe(false);
    expect(isNarrowBlockedPath("/docs/abc")).toBe(false);
    expect(isNarrowBlockedPath("/tables/abc")).toBe(false);
    expect(isNarrowBlockedPath("/more/shortcuts")).toBe(true);
    expect(isNarrowBlockedPath("/more/git")).toBe(true);
    expect(isNarrowBlockedPath("/more/general")).toBe(false);
  });

  it("keeps chat / files / messages / conversation admin / allowed settings", () => {
    expect(isNarrowBlockedPath("/")).toBe(false);
    expect(isNarrowBlockedPath("/conversations")).toBe(false);
    expect(isNarrowBlockedPath("/conversations/abc")).toBe(false);
    expect(isNarrowBlockedPath("/files")).toBe(false);
    expect(isNarrowBlockedPath("/messages")).toBe(false);
    expect(isNarrowBlockedPath("/more")).toBe(false);
    expect(isNarrowBlockedPath("/more/account")).toBe(false);
    expect(isNarrowBlockedPath("/more/about")).toBe(false);
    expect(isNarrowBlockedPath("/more/sponsor")).toBe(false);
    expect(isNarrowBlockedPath("/more/subscription")).toBe(false);
    // 旧书签 /more/feedback 走 router 重定向到关于，不能进 NarrowBlocked。
    expect(isNarrowBlockedPath("/more/feedback")).toBe(false);
  });
});

describe("narrowBlockedRedirect", () => {
  it("sends hidden settings to /more and the rest home", () => {
    expect(narrowBlockedRedirect("/more/git")).toBe("/more");
    expect(narrowBlockedRedirect("/toolbox")).toBe("/");
  });
});

describe("isNarrowHiddenPaletteId", () => {
  it("hides toolbox and conversation admin only when restrictNarrow", () => {
    expect(
      isNarrowHiddenPaletteId("nav-toolbox", {
        restrictNarrow: true,
        forceLightTheme: false,
      }),
    ).toBe(true);
    expect(
      isNarrowHiddenPaletteId("nav-toolbox", {
        restrictNarrow: false,
        forceLightTheme: false,
      }),
    ).toBe(false);
  });

  it("hides dark theme when forceLightTheme", () => {
    expect(
      isNarrowHiddenPaletteId("theme-dark", {
        restrictNarrow: false,
        forceLightTheme: true,
      }),
    ).toBe(true);
    expect(
      isNarrowHiddenPaletteId("nav-files", {
        restrictNarrow: true,
        forceLightTheme: true,
      }),
    ).toBe(false);
  });

  it("keeps 设置 · 通用 on narrow and hides zoom commands", () => {
    expect(
      isNarrowHiddenPaletteId("nav-settings-general", {
        restrictNarrow: true,
        forceLightTheme: false,
      }),
    ).toBe(false);
    expect(
      isNarrowHiddenPaletteId("zoom-in", {
        restrictNarrow: true,
        forceLightTheme: false,
      }),
    ).toBe(true);
  });

  it("does not hide 新建对话 on narrow", () => {
    expect(
      isNarrowHiddenPaletteId("new-conversation", {
        restrictNarrow: true,
        forceLightTheme: false,
      }),
    ).toBe(false);
  });
});
