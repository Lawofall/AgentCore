// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { hasHostOps, hasLocalMcp } from "../capabilities";

afterEach(() => {
  vi.unstubAllGlobals();
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
