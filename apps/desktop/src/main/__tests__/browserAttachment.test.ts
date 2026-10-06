/**
 * Local 浏览器 Attachment：hide=脱离、过期 show 拒、ensurePageKind 不因 wasActive 复活；
 * Bridge mutation（navigate/click/type/scroll）成功 data 含 elements + snapshot_version。
 */
import { WebContentsView } from "electron";
import { beforeEach, describe, expect, it, vi } from "vitest";

const ELEMENTS_TREE = "[e1] button: Go\n[e2] link: More";

vi.mock("electron", () => ({
  BrowserWindow: {
    getFocusedWindow: () => null,
    getAllWindows: () => [],
  },
  WebContentsView: vi.fn().mockImplementation(() => {
    let url = "about:blank";
    let debuggerAttached = false;
    return {
      webContents: {
        isDestroyed: () => false,
        on: vi.fn(),
        once: vi.fn(),
        removeListener: vi.fn(),
        loadURL: vi.fn(async (next: string) => {
          url = next;
        }),
        getURL: () => url,
        getTitle: () => "",
        navigationHistory: {
          canGoBack: () => false,
          canGoForward: () => false,
        },
        close: vi.fn(),
        isLoadingMainFrame: () => false,
        reload: vi.fn(),
        setWindowOpenHandler: vi.fn(),
        debugger: {
          isAttached: () => debuggerAttached,
          attach: vi.fn(() => {
            debuggerAttached = true;
          }),
          detach: vi.fn(() => {
            debuggerAttached = false;
          }),
          sendCommand: vi.fn(async () => ({})),
        },
        executeJavaScript: vi.fn(async (code: string) => {
          if (typeof code === "string" && code.includes("querySelectorAll")) {
            return ELEMENTS_TREE;
          }
          // click / type 回执探针须先于 password autocomplete 判定
          if (typeof code === "string" && code.includes("was_disabled")) {
            return { was_disabled: false, role: "button", name: "Go" };
          }
          if (typeof code === "string" && code.includes("masked")) {
            return { chars: 2, masked: false, text: "hi" };
          }
          if (
            typeof code === "string" &&
            (code.includes("selectNodeContents") ||
              code.includes("setSelectionRange") ||
              code.includes("el.select"))
          ) {
            return true;
          }
          if (typeof code === "string" && code.includes("autocomplete")) {
            return false;
          }
          return undefined;
        }),
      },
      setVisible: vi.fn(),
      setBounds: vi.fn(),
      getBounds: () => ({ x: 0, y: 0, width: 100, height: 100 }),
    };
  }),
  session: {
    fromPartition: vi.fn(() => ({
      setPermissionRequestHandler: vi.fn(),
      setPermissionCheckHandler: vi.fn(),
      protocol: { handle: vi.fn(), isProtocolHandled: () => false },
      on: vi.fn(),
    })),
  },
  shell: { openExternal: vi.fn() },
}));

vi.mock("../browser/workspace-protocol", () => ({
  registerWorkspaceProtocolFor: vi.fn(),
}));

import {
  advanceAttachmentGenerationForTests,
  bridgeDispatchLocalBrowser,
  closeAllLocalBrowserPages,
  hideLocalBrowserPages,
  localBrowserActivePageIdForTests,
  localBrowserAttachmentGenerationForTests,
  localBrowserPageVisibleForTests,
  navigateLocalBrowserPage,
  resetLegacyBrowserClearForTests,
  setBeforeAttachCheckForTests,
  showLocalBrowserPage,
} from "../browser/host";

const BOUNDS = { x: 10, y: 20, width: 400, height: 300 };

function mockWin() {
  return {
    isDestroyed: () => false,
    contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
    once: vi.fn(),
    webContents: { send: vi.fn() },
  } as never;
}

describe("Local browser Attachment", () => {
  beforeEach(() => {
    closeAllLocalBrowserPages();
    resetLegacyBrowserClearForTests();
    setBeforeAttachCheckForTests(null);
  });

  it("hide 清 active 且视图不可见", () => {
    const win = mockWin();
    expect(showLocalBrowserPage(win, "page-1", BOUNDS, "conv-1")).toEqual({
      ok: true,
      url: "about:blank",
      title: "",
      canGoBack: false,
      canGoForward: false,
    });
    expect(localBrowserActivePageIdForTests()).toBe("page-1");
    expect(localBrowserPageVisibleForTests("page-1")).toBe(true);

    const genBefore = localBrowserAttachmentGenerationForTests();
    hideLocalBrowserPages();
    expect(localBrowserActivePageIdForTests()).toBeNull();
    expect(localBrowserPageVisibleForTests("page-1")).toBe(false);
    expect(localBrowserAttachmentGenerationForTests()).toBeGreaterThan(
      genBefore,
    );
    const view = vi.mocked(WebContentsView).mock.results.at(-1)?.value as {
      setBounds: ReturnType<typeof vi.fn>;
    };
    expect(view.setBounds).toHaveBeenLastCalledWith({
      x: 0,
      y: 0,
      width: 1280,
      height: 800,
    });
    expect(WebContentsView).toHaveBeenCalledWith(
      expect.objectContaining({
        webPreferences: expect.objectContaining({
          backgroundThrottling: false,
          focusOnNavigation: false,
        }),
      }),
    );
  });

  it("hide 后再 show 才可见", () => {
    const win = mockWin();
    showLocalBrowserPage(win, "page-1", BOUNDS, "conv-1");
    hideLocalBrowserPages();
    expect(localBrowserPageVisibleForTests("page-1")).toBe(false);
    expect(localBrowserActivePageIdForTests()).toBeNull();

    expect(showLocalBrowserPage(win, "page-1", BOUNDS, "conv-1")).toEqual({
      ok: true,
      url: "about:blank",
      title: "",
      canGoBack: false,
      canGoForward: false,
    });
    expect(localBrowserActivePageIdForTests()).toBe("page-1");
    expect(localBrowserPageVisibleForTests("page-1")).toBe(true);
  });

  it("ensurePageKind 在 detached 时不点亮（非 hide 后 wasActive 复活）", () => {
    const win = mockWin();
    showLocalBrowserPage(win, "page-1", BOUNDS, "conv-1");
    hideLocalBrowserPages();
    expect(localBrowserActivePageIdForTests()).toBeNull();
    expect(localBrowserPageVisibleForTests("page-1")).toBe(false);

    // 换 kind → ensurePageKind 销毁重建；已脱离则不得 setVisible(true)
    expect(
      navigateLocalBrowserPage(
        "page-1",
        "workspace://conv-1/site/index.html",
        "conv-1",
      ),
    ).toEqual({ ok: true });
    expect(localBrowserActivePageIdForTests()).toBeNull();
    expect(localBrowserPageVisibleForTests("page-1")).toBe(false);
  });

  it("过期 show（generation 已 bump）拒", () => {
    const win = mockWin();
    setBeforeAttachCheckForTests(() => {
      advanceAttachmentGenerationForTests();
    });
    expect(showLocalBrowserPage(win, "page-stale", BOUNDS, "conv-1")).toEqual({
      ok: false,
      reason: "attachment_stale",
    });
    expect(localBrowserActivePageIdForTests()).toBeNull();
    expect(localBrowserPageVisibleForTests("page-stale")).toBe(false);
  });
});

describe("Local bridge mutation returns elements", () => {
  beforeEach(() => {
    closeAllLocalBrowserPages();
    resetLegacyBrowserClearForTests();
    setBeforeAttachCheckForTests(null);
  });

  function seedPage(pageId = "page-mut") {
    const win = mockWin();
    expect(showLocalBrowserPage(win, pageId, BOUNDS, "conv-1")).toMatchObject({
      ok: true,
    });
    return pageId;
  }

  it("navigate/click/type/scroll 成功 data 含 elements + snapshot_version", async () => {
    const pageId = seedPage();

    const nav = await bridgeDispatchLocalBrowser(
      pageId,
      "navigate",
      { url: "https://example.com/", capture: false },
      "conv-1",
    );
    expect(nav).toMatchObject({
      ok: true,
      data: {
        elements: ELEMENTS_TREE,
        snapshot_version: 1,
        aria: "",
      },
    });

    const clicked = await bridgeDispatchLocalBrowser(
      pageId,
      "click",
      { ref: "e1", snapshot_version: 1, capture: false },
      "conv-1",
    );
    expect(clicked).toMatchObject({
      ok: true,
      data: {
        elements: ELEMENTS_TREE,
        snapshot_version: 2,
        aria: "",
        clicked: {
          ref: "e1",
          was_disabled: false,
          role: "button",
          name: "Go",
        },
      },
    });

    const typed = await bridgeDispatchLocalBrowser(
      pageId,
      "type",
      { ref: "e2", text: "hi", snapshot_version: 2, capture: false },
      "conv-1",
    );
    expect(typed).toMatchObject({
      ok: true,
      data: {
        elements: ELEMENTS_TREE,
        snapshot_version: 3,
        aria: "",
        typed: {
          ref: "e2",
          requested_chars: 2,
          actual_chars: 2,
          matched: true,
          method: "cdp_insertText",
        },
      },
    });

    const scrolled = await bridgeDispatchLocalBrowser(
      pageId,
      "scroll",
      { dy: 100, capture: false },
      "conv-1",
    );
    expect(scrolled).toMatchObject({
      ok: true,
      data: {
        elements: ELEMENTS_TREE,
        snapshot_version: 4,
        aria: "",
      },
    });
  });

  it("snapshot 仍返回 elements；version 过期 / password_blocked 不 bump", async () => {
    const pageId = seedPage();
    const snap = await bridgeDispatchLocalBrowser(
      pageId,
      "snapshot",
      {},
      "conv-1",
    );
    expect(snap).toMatchObject({
      ok: true,
      data: {
        elements: ELEMENTS_TREE,
        snapshot_version: 1,
        aria: "",
      },
    });

    const stale = await bridgeDispatchLocalBrowser(
      pageId,
      "click",
      { ref: "e1", snapshot_version: 0, capture: false },
      "conv-1",
    );
    expect(stale.ok).toBe(false);
    expect(String((stale as { error?: string }).error)).toContain("版本过期");

    const { WebContentsView } = await import("electron");
    const lastView = vi.mocked(WebContentsView).mock.results.at(-1)?.value as {
      webContents: { executeJavaScript: ReturnType<typeof vi.fn> };
    };
    lastView.webContents.executeJavaScript.mockImplementation(
      async (code: string) => {
        if (typeof code === "string" && code.includes("querySelectorAll")) {
          return ELEMENTS_TREE;
        }
        if (typeof code === "string" && code.includes("autocomplete")) {
          return true;
        }
        return undefined;
      },
    );

    const blocked = await bridgeDispatchLocalBrowser(
      pageId,
      "type",
      { ref: "e1", text: "secret", snapshot_version: 1, capture: false },
      "conv-1",
    );
    expect(blocked.ok).toBe(false);
    expect(String((blocked as { error?: string }).error)).toContain(
      "password_blocked",
    );

    // snapshot_version stays at 1（stale / password 路径未 bump）
    const again = await bridgeDispatchLocalBrowser(
      pageId,
      "click",
      { ref: "e1", snapshot_version: 1, capture: false },
      "conv-1",
    );
    expect(again).toMatchObject({
      ok: true,
      data: { snapshot_version: 2, elements: ELEMENTS_TREE },
    });
  });
});
