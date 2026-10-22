// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  hasHostOps,
  hasLocalMcp,
  hasNativeMediaCapture,
} from "../capabilities";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("hasNativeMediaCapture", () => {
  it("is on only for the Capacitor shell", () => {
    vi.stubGlobal("window", { __NATIVE__: true, __WEB__: true });
    expect(hasNativeMediaCapture()).toBe(true);
    vi.stubGlobal("window", { __WEB__: true });
    expect(hasNativeMediaCapture()).toBe(false);
    vi.stubGlobal("window", {});
    expect(hasNativeMediaCapture()).toBe(false);
  });
});

describe("hasHostOps / hasLocalMcp", () => {
  it("desktop preload counts as present", () => {
    vi.stubGlobal("window", {
      hostApi: { runOp: vi.fn() },
      mcpApi: {},
    });
    expect(hasHostOps()).toBe(true);
    expect(hasLocalMcp()).toBe(true);
  });

  it("web runtime is not host/mcp capable even with a stub", () => {
    vi.stubGlobal("window", {
      __WEB__: true,
      hostApi: { runOp: vi.fn() },
      mcpApi: {},
    });
    expect(hasHostOps()).toBe(false);
    expect(hasLocalMcp()).toBe(false);
  });

  it("missing preload is not capable", () => {
    vi.stubGlobal("window", {});
    expect(hasHostOps()).toBe(false);
    expect(hasLocalMcp()).toBe(false);
  });
});
