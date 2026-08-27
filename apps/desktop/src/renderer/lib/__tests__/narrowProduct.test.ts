import { describe, expect, it } from "vitest";
import {
  isNarrowBlockedPath,
  isNarrowHiddenPaletteId,
  narrowBlockedRedirect,
} from "../narrowProduct";

describe("isNarrowBlockedPath", () => {
  it("blocks toolbox / whiteboard / conversation admin", () => {
    expect(isNarrowBlockedPath("/toolbox")).toBe(true);
    expect(isNarrowBlockedPath("/toolbox/manual/intro")).toBe(true);
    expect(isNarrowBlockedPath("/whiteboard/abc")).toBe(true);
    expect(isNarrowBlockedPath("/conversations")).toBe(true);
    expect(isNarrowBlockedPath("/more/shortcuts")).toBe(true);
  });

  it("keeps chat / files / messages / allowed settings", () => {
    expect(isNarrowBlockedPath("/")).toBe(false);
    expect(isNarrowBlockedPath("/conversations/abc")).toBe(false);
    expect(isNarrowBlockedPath("/files")).toBe(false);
    expect(isNarrowBlockedPath("/messages")).toBe(false);
    expect(isNarrowBlockedPath("/more")).toBe(false);
    expect(isNarrowBlockedPath("/more/account")).toBe(false);
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
});
