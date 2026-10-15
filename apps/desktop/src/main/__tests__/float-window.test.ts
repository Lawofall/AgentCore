/**
 * 真 OS 浮窗生命周期：上限 8、同 tab 聚焦不双开、closed 原因、退出清理、
 * Windows 任务栏分组（skipTaskbar / setAppDetails）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

type Listener = (...args: unknown[]) => void;

const { BrowserWindowCtor, created, mainSend, ipcHandle } = vi.hoisted(() => {
  const created: ReturnType<typeof createMockWin>[] = [];
  const mainSend = vi.fn();
  const ipcHandle = vi.fn();

  function createMockWin() {
    const handlers = new Map<string, Listener[]>();
    let focused = false;
    const win = {
      isDestroyed: vi.fn(() => false),
      isMinimized: vi.fn(() => false),
      isFocused: vi.fn(() => focused),
      restore: vi.fn(),
      focus: vi.fn(() => {
        focused = true;
      }),
      setTitle: vi.fn(),
      setAppDetails: vi.fn(),
      show: vi.fn(),
      hide: vi.fn(),
      isVisible: vi.fn(() => true),
      moveAbove: vi.fn(),
      getMediaSourceId: vi.fn(() => "window:float:0"),
      minimize: vi.fn(),
      setParentWindow: vi.fn(),
      getParentWindow: vi.fn(() => null),
      close: vi.fn(() => {
        for (const cb of handlers.get("closed") ?? []) cb();
      }),
      destroy: vi.fn(() => {
        for (const cb of handlers.get("closed") ?? []) cb();
      }),
      removeAllListeners: vi.fn((event?: string) => {
        if (event) handlers.delete(event);
        else handlers.clear();
      }),
      once: vi.fn((event: string, cb: Listener) => {
        if (event === "ready-to-show") cb();
      }),
      on: vi.fn((event: string, cb: Listener) => {
        const list = handlers.get(event) ?? [];
        list.push(cb);
        handlers.set(event, list);
      }),
      loadURL: vi.fn().mockResolvedValue(undefined),
      webContents: {
        setWindowOpenHandler: vi.fn(),
        on: vi.fn(),
        send: vi.fn(),
      },
    };
    return win;
  }

  const BrowserWindowCtor = vi.fn((_opts?: Record<string, unknown>) => {
    const win = createMockWin();
    created.push(win);
    return win;
  });

  return { BrowserWindowCtor, created, mainSend, ipcHandle, createMockWin };
});

vi.mock("electron", () => ({
  BrowserWindow: BrowserWindowCtor,
  ipcMain: { handle: ipcHandle, on: vi.fn() },
  shell: { openExternal: vi.fn() },
}));

import { FLOAT_WINDOW_MAX } from "@shared/float-window-contract";
import {
  buildFloatHashRoute,
  configureFloatWindows,
  destroyAllFloatWindows,
  destroyFloatWindow,
  dockFloatWindow,
  floatWindowCount,
  hasFloatWindow,
  isManagedFloatWindow,
  minimizeBrowserWindow,
  openFloatWindow,
  resetFloatWindowsForTests,
} from "../float-window";

const mainGetBounds = vi.fn(() => ({
  x: 100,
  y: 80,
  width: 1280,
  height: 800,
}));

beforeEach(() => {
  resetFloatWindowsForTests();
  created.length = 0;
  BrowserWindowCtor.mockClear();
  mainSend.mockClear();
  mainGetBounds.mockClear();
  mainGetBounds.mockReturnValue({
    x: 100,
    y: 80,
    width: 1280,
    height: 800,
  });
  configureFloatWindows({
    getMainWindow: () =>
      ({
        isDestroyed: () => false,
        isMinimized: () => false,
        restore: vi.fn(),
        getBounds: mainGetBounds,
        getMediaSourceId: () => "window:main:0",
        webContents: { send: mainSend },
      }) as never,
    buildFloatUrl: (cid, tab) =>
      `app://agentcore/index.html${buildFloatHashRoute(cid, tab)}`,
    allowedNavigationBase: "app://agentcore",
    preloadPath: "/preload/index.js",
  });
});

describe("buildFloatHashRoute", () => {
  it("uses #/float?cid=&tab= (URL-encoded)", () => {
    expect(buildFloatHashRoute("conv-1", "run:abc")).toBe(
      "#/float?cid=conv-1&tab=run%3Aabc",
    );
  });
});

describe("openFloatWindow", () => {
  it("creates a window with shared preload and float hash URL", () => {
    expect(
      openFloatWindow({
        tabId: "tab-1",
        conversationId: "cid-1",
        title: "Run 1",
      }),
    ).toBe(true);
    expect(BrowserWindowCtor).toHaveBeenCalledTimes(1);
    const opts = BrowserWindowCtor.mock.calls[0]?.[0] as {
      webPreferences: { preload: string };
      title: string;
      skipTaskbar: boolean;
      frame: boolean;
      parent?: unknown;
      modal?: boolean;
      type?: string;
    };
    expect(opts.webPreferences.preload).toBe("/preload/index.js");
    expect(opts.title).toBe("Run 1");
    expect(opts.frame).toBe(false);
    expect(opts.skipTaskbar).toBe(false);
    expect(opts.parent).toBeTruthy();
    expect(
      (BrowserWindowCtor.mock.calls[0]?.[0] as { minimizable?: boolean })
        .minimizable,
    ).toBe(false);
    expect(opts.modal).toBeUndefined();
    expect(opts.type).toBeUndefined();
    expect(created[0]?.loadURL).toHaveBeenCalledWith(
      "app://agentcore/index.html#/float?cid=cid-1&tab=tab-1",
    );
    expect(floatWindowCount()).toBe(1);
  });

  it("centers relative to main with offset when bounds omitted", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "Run 1",
    });
    const opts = BrowserWindowCtor.mock.calls[0]?.[0] as {
      x: number;
      y: number;
      width: number;
      height: number;
    };
    // main (100,80,1280×800) → center default 640×800 + cascade×48 (1st → 48)
    expect(opts.width).toBe(640);
    expect(opts.height).toBe(800);
    expect(opts.x).toBe(100 + Math.round((1280 - 640) / 2) + 48);
    expect(opts.y).toBe(80 + Math.round((800 - 800) / 2) + 48);
    expect(opts.x).not.toBe(0);
    expect(opts.y).not.toBe(0);
  });

  it("cascades subsequent windows so they are not stacked on the same origin", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A",
    });
    openFloatWindow({
      tabId: "tab-2",
      conversationId: "cid-1",
      title: "B",
    });
    openFloatWindow({
      tabId: "tab-3",
      conversationId: "cid-1",
      title: "C",
    });
    const baseX = 100 + Math.round((1280 - 640) / 2);
    const baseY = 80 + Math.round((800 - 800) / 2);
    const positions = [0, 1, 2].map((i) => {
      const opts = BrowserWindowCtor.mock.calls[i]?.[0] as {
        x: number;
        y: number;
      };
      return opts;
    });
    expect(positions[0]).toMatchObject({ x: baseX + 48, y: baseY + 48 });
    expect(positions[1]).toMatchObject({ x: baseX + 96, y: baseY + 96 });
    expect(positions[2]).toMatchObject({ x: baseX + 144, y: baseY + 144 });
  });

  it("parents each float to the main window and disables minimize", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A",
    });
    openFloatWindow({
      tabId: "tab-2",
      conversationId: "cid-1",
      title: "B",
    });
    for (let i = 0; i < 2; i++) {
      const opts = BrowserWindowCtor.mock.calls[i]?.[0] as {
        parent?: { getMediaSourceId?: () => string };
        minimizable?: boolean;
      };
      expect(opts.parent?.getMediaSourceId?.()).toBe("window:main:0");
      expect(opts.minimizable).toBe(false);
    }
  });

  it("minimizeBrowserWindow is a no-op for managed floats", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A",
    });
    const win = created[0];
    expect(win).toBeDefined();
    expect(isManagedFloatWindow(win as never)).toBe(true);
    minimizeBrowserWindow(win as never);
    expect(win.minimize).not.toHaveBeenCalled();
    expect(win.hide).not.toHaveBeenCalled();
  });

  it("re-open shows a previously hidden float", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A",
    });
    const win = created[0];
    expect(win).toBeDefined();
    win.isVisible.mockReturnValue(false);
    win.show.mockClear();
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A2",
    });
    expect(win.show).toHaveBeenCalled();
    expect(BrowserWindowCtor).toHaveBeenCalledTimes(1);
  });

  it("uses explicit bounds when provided", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "Run 1",
      bounds: { x: 40, y: 50, width: 500, height: 600 },
    });
    const opts = BrowserWindowCtor.mock.calls[0]?.[0] as {
      x: number;
      y: number;
      width: number;
      height: number;
    };
    expect(opts).toMatchObject({ x: 40, y: 50, width: 500, height: 600 });
    expect(mainGetBounds).not.toHaveBeenCalled();
  });

  it("on Windows sets AppUserModelID via setAppDetails", () => {
    const prev = process.platform;
    Object.defineProperty(process, "platform", { value: "win32" });
    try {
      openFloatWindow({
        tabId: "tab-1",
        conversationId: "cid-1",
        title: "Run 1",
      });
      expect(created[0]?.setAppDetails).toHaveBeenCalledWith({
        appId: "xyz.fashitianxia.gagent",
      });
    } finally {
      Object.defineProperty(process, "platform", { value: prev });
    }
  });

  it("focuses existing same tabId instead of opening a second window", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A",
    });
    expect(
      openFloatWindow({
        tabId: "tab-1",
        conversationId: "cid-1",
        title: "A2",
      }),
    ).toBe(true);
    expect(BrowserWindowCtor).toHaveBeenCalledTimes(1);
    expect(created[0]?.focus).toHaveBeenCalled();
    expect(created[0]?.setTitle).toHaveBeenCalledWith("A2");
    expect(floatWindowCount()).toBe(1);
  });

  it("skips focus on reuse when the window is already focused", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A",
    });
    created[0]?.focus.mockClear();
    // Simulate OS already focusing this window (e.g. after first open).
    created[0]?.isFocused.mockReturnValue(true);
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid-1",
      title: "A3",
    });
    expect(created[0]?.focus).not.toHaveBeenCalled();
    expect(created[0]?.setTitle).toHaveBeenCalledWith("A3");
  });

  it("rejects open beyond FLOAT_WINDOW_MAX", () => {
    for (let i = 0; i < FLOAT_WINDOW_MAX; i++) {
      expect(
        openFloatWindow({
          tabId: `tab-${i}`,
          conversationId: "cid",
          title: `T${i}`,
        }),
      ).toBe(true);
    }
    expect(
      openFloatWindow({
        tabId: "tab-extra",
        conversationId: "cid",
        title: "Extra",
      }),
    ).toBe(false);
    expect(floatWindowCount()).toBe(FLOAT_WINDOW_MAX);
    expect(BrowserWindowCtor).toHaveBeenCalledTimes(FLOAT_WINDOW_MAX);
  });
});

describe("closed reasons", () => {
  it("dock → closed reason dock", () => {
    openFloatWindow({
      tabId: "tab-1",
      conversationId: "cid",
      title: "T",
    });
    dockFloatWindow("tab-1");
    expect(mainSend).toHaveBeenCalledWith("float-window:closed", {
      tabId: "tab-1",
      reason: "dock",
    });
    expect(hasFloatWindow("tab-1")).toBe(false);
  });

  it("destroy → closed reason destroy", () => {
    openFloatWindow({
      tabId: "tab-2",
      conversationId: "cid",
      title: "T",
    });
    destroyFloatWindow("tab-2");
    expect(mainSend).toHaveBeenCalledWith("float-window:closed", {
      tabId: "tab-2",
      reason: "destroy",
    });
  });

  it("user OS close → closed reason user", () => {
    openFloatWindow({
      tabId: "tab-3",
      conversationId: "cid",
      title: "T",
    });
    // Simulate title-bar / windowApi.close → BrowserWindow.close without pending reason.
    created[0]?.close();
    expect(mainSend).toHaveBeenCalledWith("float-window:closed", {
      tabId: "tab-3",
      reason: "user",
    });
  });
});

describe("destroyAllFloatWindows", () => {
  it("clears all floats without notifying main", () => {
    openFloatWindow({
      tabId: "a",
      conversationId: "cid",
      title: "A",
    });
    openFloatWindow({
      tabId: "b",
      conversationId: "cid",
      title: "B",
    });
    mainSend.mockClear();
    destroyAllFloatWindows();
    expect(floatWindowCount()).toBe(0);
    expect(mainSend).not.toHaveBeenCalled();
  });
});
