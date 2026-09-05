/**
 * 沙箱浏览器输入客户端 (services/browserInput.ts) 单测：
 * - REST：input 端点 + body 形；空批不发；路径编码 conversation id。
 * - 坐标换算 toFrameSpace：object-contain 缩放 + 信箱留白 + 钳制 + 非法尺寸兜底。
 * - 输入批处理 createInputBatcher：定时 flush、move 合并、commit 立即 flush、stop 收口、发失败吞掉。
 * - 修饰键 modifiersOf。
 * mock `@/services/api` 的 api 方法。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  };
});

import { api } from "@/services/api";
import {
  type BrowserInputEvent,
  createInputBatcher,
  modifiersOf,
  sendBrowserInput,
  toFrameSpace,
} from "../browserInput";

const mockPost = vi.mocked(api.post);

beforeEach(() => {
  mockPost.mockReset().mockResolvedValue({ ok: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("browserInput · REST", () => {
  it("posts input events in a batch", async () => {
    const events: BrowserInputEvent[] = [
      { kind: "mouse", type: "down", x: 10, y: 20, button: 0, click_count: 1 },
      { kind: "key", type: "down", key: "a", code: "KeyA" },
    ];
    await sendBrowserInput("c1", events);
    expect(mockPost).toHaveBeenCalledWith(
      "/v1/conversations/c1/browser/input",
      { events },
    );
  });

  it("does not POST an empty input batch", async () => {
    await sendBrowserInput("c1", []);
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("encodes the conversation id in the path", async () => {
    await sendBrowserInput("a/b?c", [{ kind: "text", text: "x" }]);
    expect(mockPost).toHaveBeenCalledWith(
      "/v1/conversations/a%2Fb%3Fc/browser/input",
      { events: [{ kind: "text", text: "x" }] },
    );
  });

  it("includes session_id when the live tab is pinned", async () => {
    await sendBrowserInput("c1", [{ kind: "text", text: "x" }], {
      sessionId: "sess-1",
    });
    expect(mockPost).toHaveBeenCalledWith(
      "/v1/conversations/c1/browser/input",
      { events: [{ kind: "text", text: "x" }], session_id: "sess-1" },
    );
  });
});

describe("toFrameSpace · object-contain 坐标换算", () => {
  it("maps the display center to the frame center (letterboxed left/right)", () => {
    // 容器 1000×500，帧 1000×1000 → scale 0.5，两侧各留白 250。
    const rect = { left: 0, top: 0, width: 1000, height: 500 };
    expect(toFrameSpace(500, 250, rect, 1000, 1000)).toEqual({
      x: 500,
      y: 500,
    });
  });

  it("maps the display center to the frame center (letterboxed top/bottom)", () => {
    // 容器 500×500，帧 1000×500 → scale 0.5，上下各留白 125。
    const rect = { left: 0, top: 0, width: 500, height: 500 };
    expect(toFrameSpace(250, 250, rect, 1000, 500)).toEqual({ x: 500, y: 250 });
  });

  it("accounts for the container's viewport offset", () => {
    const rect = { left: 100, top: 50, width: 1000, height: 500 };
    expect(toFrameSpace(600, 300, rect, 1000, 1000)).toEqual({
      x: 500,
      y: 500,
    });
  });

  it("clamps points in the letterbox / outside the frame to the edges", () => {
    const rect = { left: 0, top: 0, width: 1000, height: 500 };
    expect(toFrameSpace(0, 250, rect, 1000, 1000)).toEqual({ x: 0, y: 500 });
    expect(toFrameSpace(1000, 250, rect, 1000, 1000)).toEqual({
      x: 1000,
      y: 500,
    });
  });

  it("returns (0,0) for a degenerate frame / zero-size container", () => {
    expect(
      toFrameSpace(5, 5, { left: 0, top: 0, width: 100, height: 100 }, 0, 0),
    ).toEqual({ x: 0, y: 0 });
    expect(
      toFrameSpace(5, 5, { left: 0, top: 0, width: 0, height: 0 }, 100, 100),
    ).toEqual({ x: 0, y: 0 });
  });
});

describe("modifiersOf", () => {
  it("collects held modifiers, omitting when none", () => {
    expect(
      modifiersOf({
        altKey: false,
        ctrlKey: true,
        metaKey: false,
        shiftKey: true,
      }),
    ).toEqual(["ctrl", "shift"]);
    expect(
      modifiersOf({
        altKey: false,
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
      }),
    ).toBeUndefined();
  });
});

describe("createInputBatcher", () => {
  it("flushes buffered events after the interval", () => {
    vi.useFakeTimers();
    const send = vi.fn().mockResolvedValue(undefined);
    const b = createInputBatcher(send, 60);
    b.push({
      kind: "mouse",
      type: "down",
      x: 1,
      y: 1,
      button: 0,
      click_count: 1,
    });
    expect(send).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith([
      { kind: "mouse", type: "down", x: 1, y: 1, button: 0, click_count: 1 },
    ]);
  });

  it("coalesces consecutive mouse moves (only the latest survives)", () => {
    vi.useFakeTimers();
    const send = vi.fn().mockResolvedValue(undefined);
    const b = createInputBatcher(send, 60);
    b.push({ kind: "mouse", type: "move", x: 1, y: 1 });
    b.push({ kind: "mouse", type: "move", x: 2, y: 2 });
    vi.advanceTimersByTime(60);
    expect(send).toHaveBeenCalledWith([
      { kind: "mouse", type: "move", x: 2, y: 2 },
    ]);
  });

  it("flushes immediately on a commit event, batching prior events", () => {
    const send = vi.fn().mockResolvedValue(undefined);
    const b = createInputBatcher(send, 60);
    b.push({
      kind: "mouse",
      type: "down",
      x: 1,
      y: 1,
      button: 0,
      click_count: 1,
    });
    expect(send).not.toHaveBeenCalled();
    b.push({ kind: "mouse", type: "up", x: 1, y: 1, button: 0 });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith([
      { kind: "mouse", type: "down", x: 1, y: 1, button: 0, click_count: 1 },
      { kind: "mouse", type: "up", x: 1, y: 1, button: 0 },
    ]);
  });

  it("stop() flushes remaining events and ignores later pushes", () => {
    const send = vi.fn().mockResolvedValue(undefined);
    const b = createInputBatcher(send, 60);
    b.push({ kind: "key", type: "down", key: "a" });
    b.stop();
    expect(send).toHaveBeenCalledTimes(1);
    b.push({ kind: "key", type: "down", key: "b" });
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("swallows send failures (best-effort, no throw)", () => {
    const send = vi.fn().mockRejectedValue(new Error("net down"));
    const b = createInputBatcher(send, 60);
    expect(() => b.push({ kind: "text", text: "secret" })).not.toThrow();
    expect(send).toHaveBeenCalledTimes(1);
  });
});
