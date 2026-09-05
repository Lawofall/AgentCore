// @vitest-environment jsdom
/**
 * M1 BrowserPanel：页签 / 地址栏 / 本机 browserApi 真导航；hydrate；关 server 页 DELETE；
 * live 仅当当前页带 serverSessionId。
 */

import type { BrowserApi, BrowserNavState } from "@shared/browser-contract";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TAB_DRAG_THRESHOLD_PX } from "@/components/ui/tab-reorder";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/components/workspace/BrowserLivePanel", () => ({
  BrowserLivePanel: ({
    conversationId,
    sessionId,
  }: {
    conversationId: string;
    sessionId?: string;
  }) => (
    <div data-testid="browser-live">
      {conversationId}:{sessionId ?? ""}
    </div>
  ),
}));

vi.mock("@/services/browserSessions", () => ({
  listBrowserSessions: vi.fn().mockResolvedValue({
    sessions: [],
    activeSessionId: null,
  }),
  closeBrowserSession: vi.fn().mockResolvedValue(undefined),
  createBrowserSession: vi.fn(),
  navigateBrowserSession: vi.fn(),
  patchBrowserSessionNav: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

vi.mock("@/services/workspace", () => ({
  openWorkspaceInBrowser: vi.fn().mockResolvedValue(undefined),
}));

import { notifyError } from "@/lib/toast";
import {
  closeBrowserSession,
  createBrowserSession,
  listBrowserSessions,
  navigateBrowserSession,
} from "@/services/browserSessions";
import { openWorkspaceInBrowser } from "@/services/workspace";
import { useBrowserSessionsStore } from "@/stores/browserSessions";
import { BrowserPanel, isLocalhostBrowserUrl } from "../BrowserPanel";

function renderPanel(ui: ReactElement) {
  return render(<TooltipProvider delayDuration={0}>{ui}</TooltipProvider>);
}

const listMock = vi.mocked(listBrowserSessions);
const closeMock = vi.mocked(closeBrowserSession);
const createMock = vi.mocked(createBrowserSession);
const navigateMock = vi.mocked(navigateBrowserSession);
const notifyMock = vi.mocked(notifyError);
const openWorkspaceMock = vi.mocked(openWorkspaceInBrowser);

function mockBrowserApi(overrides: Partial<BrowserApi> = {}): BrowserApi {
  return {
    show: vi.fn().mockResolvedValue({
      ok: true,
      url: "about:blank",
      title: "",
      canGoBack: false,
      canGoForward: false,
    }),
    setBounds: vi.fn(),
    hide: vi.fn().mockResolvedValue(undefined),
    navigate: vi.fn().mockResolvedValue({ ok: true }),
    openWorkspaceHtml: vi.fn().mockResolvedValue({ ok: true }),
    reload: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    close: vi.fn(),
    closeConversation: vi.fn().mockResolvedValue({ ok: true }),
    openExternal: vi.fn().mockResolvedValue({ ok: true }),
    onNavState: vi.fn().mockReturnValue(() => {}),
    onOpenTab: vi.fn().mockReturnValue(() => {}),
    ...overrides,
  };
}

function submitAddressBar(input: HTMLElement) {
  const form = input.closest("form");
  expect(form).not.toBeNull();
  if (!form) throw new Error("expected address form");
  fireEvent.submit(form);
}

function firstPage(conversationId: string) {
  const page = useBrowserSessionsStore.getState().pagesFor(conversationId)[0];
  expect(page).toBeDefined();
  if (!page) throw new Error("expected browser page");
  return page;
}

async function waitForPages(conversationId: string, min = 1) {
  await waitFor(() => {
    expect(
      useBrowserSessionsStore.getState().pagesFor(conversationId).length,
    ).toBeGreaterThanOrEqual(min);
  });
}

beforeEach(() => {
  useBrowserSessionsStore.setState({
    pages: [],
    activePageId: null,
    activePageIdByConversation: {},
  });
  listMock.mockReset();
  closeMock.mockReset();
  createMock.mockReset();
  navigateMock.mockReset();
  notifyMock.mockReset();
  openWorkspaceMock.mockReset();
  openWorkspaceMock.mockResolvedValue(undefined);
  listMock.mockResolvedValue({ sessions: [], activeSessionId: null });
  closeMock.mockResolvedValue(undefined);
  createMock.mockResolvedValue({
    sessionId: "sess-created",
    conversationId: "conv-1",
    hostKind: "sandbox",
    control: "agent",
    runId: null,
    createdAt: 1,
    lastUsed: 1,
  });
  navigateMock.mockResolvedValue({
    sessionId: "sess-created",
    conversationId: "conv-1",
    hostKind: "sandbox",
    control: "agent",
    runId: null,
    createdAt: 1,
    lastUsed: 2,
    url: "https://example.com",
  });
  window.browserApi = undefined;
  // jsdom 无 ResizeObserver；本机 Host 路径会挂观察器。
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(() => {
  cleanup();
  window.browserApi = undefined;
  (window as unknown as { fsApi?: unknown }).fsApi = undefined;
});

describe("BrowserPanel", () => {
  it("shows chrome + browse placeholder when there is no live session and no browserApi", async () => {
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(screen.getByLabelText("地址栏")).toBeTruthy();
    expect(screen.getByLabelText("新标签页")).toBeTruthy();
    await waitFor(() => {
      expect(
        useBrowserSessionsStore.getState().pagesFor("conv-1"),
      ).toHaveLength(1);
    });
    expect(screen.getByText("输入地址开始浏览")).toBeTruthy();
    expect(screen.queryByText("暂无浏览器活动")).toBeNull();
    expect(screen.queryByTestId("browser-live")).toBeNull();
    await waitFor(() => {
      expect(listMock).toHaveBeenCalledWith("conv-1");
    });
  });

  it("does not mount BrowserLivePanel for blank local page even when liveAvailable", async () => {
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={true} />);
    await waitFor(() => {
      expect(
        useBrowserSessionsStore.getState().pagesFor("conv-1").length,
      ).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId("browser-live")).toBeNull();
  });

  it("mounts BrowserLivePanel when active page has serverSessionId (no liveAvailable gate)", () => {
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "browser-server:sess-live",
          url: "",
          title: "浏览器 · sandbox · sess-live",
          conversationId: "conv-1",
          serverSessionId: "sess-live",
          hostKind: "sandbox",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-live",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(screen.getByTestId("browser-live").textContent).toBe(
      "conv-1:sess-live",
    );
  });

  it("prefers WebContents over Live for local hostKind when browserApi exists", () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "browser-server:sess-local",
          url: "https://example.com",
          title: "浏览器 · local · sess-local",
          conversationId: "conv-1",
          serverSessionId: "sess-local",
          hostKind: "local",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-local",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={true} />);
    expect(screen.queryByTestId("browser-live")).toBeNull();
    expect(screen.queryByText("接管")).toBeNull();
    expect(screen.queryByText("归还控制")).toBeNull();
    expect(screen.getByText("页面加载中…")).toBeTruthy();
  });

  it("mounts BrowserLivePanel for local hostKind when browserApi is absent (remote viewer)", () => {
    window.browserApi = undefined;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "browser-server:sess-local-remote",
          url: "https://example.com",
          title: "浏览器 · local · sess-local-remote",
          conversationId: "conv-1",
          serverSessionId: "sess-local-remote",
          hostKind: "local",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-local-remote",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={true} />);
    expect(screen.getByTestId("browser-live").textContent).toBe(
      "conv-1:sess-local-remote",
    );
  });

  it("creates another local blank page via the new-tab button (no POST create)", async () => {
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitFor(() => {
      expect(
        useBrowserSessionsStore.getState().pagesFor("conv-1"),
      ).toHaveLength(1);
    });
    fireEvent.click(screen.getByLabelText("新标签页"));
    expect(useBrowserSessionsStore.getState().pagesFor("conv-1")).toHaveLength(
      2,
    );
    expect(
      useBrowserSessionsStore
        .getState()
        .pagesFor("conv-1")
        .every((p) => !p.serverSessionId),
    ).toBe(true);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(closeMock).not.toHaveBeenCalled();
  });

  it("hydrates server sessions into tabs and activates them over blank", async () => {
    listMock.mockResolvedValue({
      sessions: [
        {
          sessionId: "sess-abc",
          conversationId: "conv-1",
          hostKind: "sandbox",
          control: "agent",
          runId: null,
          createdAt: 1,
          lastUsed: 1,
        },
      ],
      activeSessionId: "sess-abc",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitFor(() => {
      const pages = useBrowserSessionsStore.getState().pagesFor("conv-1");
      expect(pages.some((p) => p.serverSessionId === "sess-abc")).toBe(true);
      expect(useBrowserSessionsStore.getState().activePageId).toBe(
        "browser-server:sess-abc",
      );
    });
  });

  it("closes a server page with DELETE then removes it", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    listMock.mockResolvedValue({ sessions: [], activeSessionId: null });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    useBrowserSessionsStore.setState((s) => ({
      pages: [
        ...s.pages,
        {
          id: "browser-server:sess-1",
          url: "",
          title: "浏览器 · sandbox · sess-1",
          conversationId: "conv-1",
          serverSessionId: "sess-1",
          hostKind: "sandbox",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-1",
    }));

    const closeBtn = await screen.findByLabelText(
      /关闭 浏览器 · sandbox · sess-1/,
    );
    fireEvent.click(closeBtn);

    expect(api.close).toHaveBeenCalledWith("sess-1");
    await waitFor(() => {
      expect(closeMock).toHaveBeenCalledWith("conv-1", "sess-1");
    });
    await waitFor(() => {
      expect(
        useBrowserSessionsStore
          .getState()
          .pagesFor("conv-1")
          .some((p) => p.serverSessionId === "sess-1"),
      ).toBe(false);
    });
  });

  it("keeps server page locally when DELETE fails", async () => {
    closeMock.mockRejectedValue(new Error("nope"));
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    useBrowserSessionsStore.setState((s) => ({
      pages: [
        ...s.pages,
        {
          id: "browser-server:sess-2",
          url: "",
          title: "浏览器 · sandbox · sess-2",
          conversationId: "conv-1",
          serverSessionId: "sess-2",
          hostKind: "sandbox",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-2",
    }));

    fireEvent.click(
      await screen.findByLabelText(/关闭 浏览器 · sandbox · sess-2/),
    );

    await waitFor(() => {
      expect(closeMock).toHaveBeenCalledWith("conv-1", "sess-2");
    });
    expect(
      useBrowserSessionsStore
        .getState()
        .pagesFor("conv-1")
        .some((p) => p.serverSessionId === "sess-2"),
    ).toBe(true);
  });

  it("navigates the active page from the address bar (store)", async () => {
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const input = screen.getByLabelText("地址栏");
    fireEvent.change(input, { target: { value: "example.com" } });
    submitAddressBar(input);
    const page = firstPage("conv-1");
    expect(page.url).toBe("https://example.com");
    expect(page.title).toBe("example.com");
    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith("conv-1", {
        hostKind: "sandbox",
        activate: true,
      });
    });
  });

  it("Web address bar: create sandbox + navigate when no browserApi", async () => {
    window.browserApi = undefined;
    // 首轮 hydrate 空 list → 本地空白；create 后再 hydrate 带回 session。
    listMock
      .mockResolvedValueOnce({ sessions: [], activeSessionId: null })
      .mockResolvedValue({
        sessions: [
          {
            sessionId: "sess-created",
            conversationId: "conv-1",
            hostKind: "sandbox",
            control: "agent",
            runId: null,
            createdAt: 1,
            lastUsed: 1,
          },
        ],
        activeSessionId: "sess-created",
      });
    createMock.mockResolvedValue({
      sessionId: "sess-created",
      conversationId: "conv-1",
      hostKind: "sandbox",
      control: "agent",
      runId: null,
      createdAt: 1,
      lastUsed: 1,
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const input = screen.getByLabelText("地址栏");
    fireEvent.change(input, { target: { value: "https://example.com" } });
    submitAddressBar(input);

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith("conv-1", {
        hostKind: "sandbox",
        activate: true,
      });
      expect(navigateMock).toHaveBeenCalledWith(
        "conv-1",
        "sess-created",
        "https://example.com",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("browser-live").textContent).toBe(
        "conv-1:sess-created",
      );
    });
  });

  it("Web address bar: rejects localhost without create", async () => {
    window.browserApi = undefined;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const input = screen.getByLabelText("地址栏");
    fireEvent.change(input, { target: { value: "http://127.0.0.1:3000" } });
    submitAddressBar(input);

    await waitFor(() => {
      expect(notifyMock).toHaveBeenCalled();
    });
    expect(createMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
    expect(isLocalhostBrowserUrl("http://localhost/")).toBe(true);
  });

  it("calls browserApi.navigate on address submit when Local host is active", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const page = firstPage("conv-1");
    const input = screen.getByLabelText("地址栏");
    fireEvent.change(input, { target: { value: "https://example.com" } });
    submitAddressBar(input);
    await waitFor(() => {
      expect(api.navigate).toHaveBeenCalledWith({
        pageId: page.id,
        url: "https://example.com",
        conversationId: "conv-1",
      });
    });
    expect(createMock).not.toHaveBeenCalled();
  });

  it("onOpenTab creates a shell tab and navigates (target=_blank path)", async () => {
    let openTabCb:
      | ((req: {
          conversationId: string;
          url: string;
          background?: boolean;
        }) => void)
      | null = null;
    const api = mockBrowserApi({
      onOpenTab: (cb) => {
        openTabCb = cb;
        return () => {
          openTabCb = null;
        };
      },
    });
    window.browserApi = api;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const before = useBrowserSessionsStore.getState().pagesFor("conv-1").length;
    expect(openTabCb).not.toBeNull();
    act(() => {
      openTabCb?.({
        conversationId: "conv-1",
        url: "https://example.com/blank-target",
      });
    });
    await waitFor(() => {
      expect(useBrowserSessionsStore.getState().pagesFor("conv-1").length).toBe(
        before + 1,
      );
      expect(api.navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "https://example.com/blank-target",
          conversationId: "conv-1",
        }),
      );
    });
  });

  it("Local host show/navigate uses bare serverSessionId when present", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        x: 10,
        y: 20,
        top: 20,
        left: 10,
        bottom: 420,
        right: 810,
        width: 800,
        height: 400,
        toJSON: () => ({}),
      } as DOMRect);
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "browser-server:sess-local",
          url: "https://example.com/agent",
          title: "Agent Page",
          conversationId: "conv-1",
          serverSessionId: "sess-local",
          hostKind: "local",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-local",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitFor(() => {
      expect(api.show).toHaveBeenCalledWith(
        expect.objectContaining({
          pageId: "sess-local",
          conversationId: "conv-1",
        }),
      );
    });
    const input = screen.getByLabelText("地址栏");
    fireEvent.change(input, { target: { value: "https://example.com/next" } });
    submitAddressBar(input);
    await waitFor(() => {
      expect(api.navigate).toHaveBeenCalledWith({
        pageId: "sess-local",
        url: "https://example.com/next",
        conversationId: "conv-1",
      });
    });
    expect(api.navigate).not.toHaveBeenCalledWith(
      expect.objectContaining({ pageId: "browser-server:sess-local" }),
    );
    expect(api.show).not.toHaveBeenCalledWith(
      expect.objectContaining({ pageId: "browser-server:sess-local" }),
    );
    rectSpy.mockRestore();
  });

  it("calls browserApi.close when closing a local page", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const page = firstPage("conv-1");
    fireEvent.click(screen.getByLabelText(`关闭 ${page.title || "新标签页"}`));
    expect(api.close).toHaveBeenCalledWith(page.id);
  });

  it("enables back when navState reports canGoBack", async () => {
    const nav = {
      cb: null as ((s: BrowserNavState) => void) | null,
    };
    const api = mockBrowserApi({
      onNavState: (cb) => {
        nav.cb = cb;
        return () => {};
      },
    });
    window.browserApi = api;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const page = firstPage("conv-1");
    expect((screen.getByLabelText("后退") as HTMLButtonElement).disabled).toBe(
      true,
    );
    nav.cb?.({
      pageId: page.id,
      url: "https://example.com/b",
      title: "Example",
      canGoBack: true,
      canGoForward: false,
    });
    await waitFor(() => {
      expect(
        (screen.getByLabelText("后退") as HTMLButtonElement).disabled,
      ).toBe(false);
    });
    fireEvent.click(screen.getByLabelText("后退"));
    expect(api.back).toHaveBeenCalledWith(page.id);
  });

  it("enables forward when navState reports canGoForward", async () => {
    const nav = {
      cb: null as ((s: BrowserNavState) => void) | null,
    };
    const api = mockBrowserApi({
      onNavState: (cb) => {
        nav.cb = cb;
        return () => {};
      },
    });
    window.browserApi = api;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitForPages("conv-1");
    const page = firstPage("conv-1");
    expect((screen.getByLabelText("前进") as HTMLButtonElement).disabled).toBe(
      true,
    );
    nav.cb?.({
      pageId: page.id,
      url: "https://example.com/a",
      title: "Example",
      canGoBack: true,
      canGoForward: true,
    });
    await waitFor(() => {
      expect(
        (screen.getByLabelText("前进") as HTMLButtonElement).disabled,
      ).toBe(false);
    });
    fireEvent.click(screen.getByLabelText("前进"));
    expect(api.forward).toHaveBeenCalledWith(page.id);
  });

  it("back/reload for serverSession page use bare session id", async () => {
    const nav = {
      cb: null as ((s: BrowserNavState) => void) | null,
    };
    const api = mockBrowserApi({
      onNavState: (cb) => {
        nav.cb = cb;
        return () => {};
      },
    });
    window.browserApi = api;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "browser-server:sess-nav",
          url: "https://example.com",
          title: "Ex",
          conversationId: "conv-1",
          serverSessionId: "sess-nav",
          hostKind: "local",
          control: "user",
        },
      ],
      activePageId: "browser-server:sess-nav",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    nav.cb?.({
      pageId: "sess-nav",
      url: "https://example.com/b",
      title: "Ex",
      canGoBack: true,
      canGoForward: false,
    });
    await waitFor(() => {
      expect(
        (screen.getByLabelText("后退") as HTMLButtonElement).disabled,
      ).toBe(false);
    });
    fireEvent.click(screen.getByLabelText("后退"));
    expect(api.back).toHaveBeenCalledWith("sess-nav");
    fireEvent.click(screen.getByLabelText("刷新"));
    expect(api.reload).toHaveBeenCalledWith("sess-nav");
  });

  it("on remount does not navigate when show returns a live host URL", async () => {
    const api = mockBrowserApi({
      show: vi.fn().mockResolvedValue({
        ok: true,
        url: "https://example.com/real",
        title: "Real",
        canGoBack: true,
        canGoForward: false,
      }),
    });
    window.browserApi = api;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "page-keep",
          url: "https://example.com/stale",
          title: "Stale",
          conversationId: "conv-1",
          serverSessionId: null,
        },
      ],
      activePageId: "page-keep",
      activePageIdByConversation: { "conv-1": "page-keep" },
    });
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        bottom: 100,
        right: 100,
        width: 100,
        height: 100,
        toJSON: () => ({}),
      });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitFor(() => {
      expect(api.show).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect((screen.getByLabelText("地址栏") as HTMLInputElement).value).toBe(
        "https://example.com/real",
      );
    });
    expect(api.navigate).not.toHaveBeenCalled();
    expect(useBrowserSessionsStore.getState().pages[0]?.url).toBe(
      "https://example.com/real",
    );
    rectSpy.mockRestore();
  });

  it("cold-restores navigate when show returns blank and store has URL", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "page-cold",
          url: "https://example.com/cold",
          title: "Cold",
          conversationId: "conv-1",
          serverSessionId: null,
        },
      ],
      activePageId: "page-cold",
      activePageIdByConversation: { "conv-1": "page-cold" },
    });
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        bottom: 100,
        right: 100,
        width: 100,
        height: 100,
        toJSON: () => ({}),
      });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    await waitFor(() => {
      expect(api.navigate).toHaveBeenCalledWith({
        pageId: "page-cold",
        url: "https://example.com/cold",
        conversationId: "conv-1",
      });
    });
    rectSpy.mockRestore();
  });

  it("disables back and refresh without browserApi", () => {
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect((screen.getByLabelText("后退") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByLabelText("刷新") as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("hides 接管 chrome on local preview without serverSessionId", () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(screen.queryByText("接管")).toBeNull();
    expect(screen.queryByText("归还控制")).toBeNull();
  });

  it("uses Local WebContents when active page has serverSessionId (non-live)", () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "browser-server:sess-local",
          url: "https://example.com",
          title: "浏览器 · local · sess-local",
          conversationId: "conv-1",
          serverSessionId: "sess-local",
          hostKind: "local",
          control: "agent",
        },
      ],
      activePageId: "browser-server:sess-local",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(screen.queryByText("接管")).toBeNull();
    expect(screen.queryByText("归还控制")).toBeNull();
    expect(screen.queryByTestId("browser-live")).toBeNull();
    expect(screen.getByText("页面加载中…")).toBeTruthy();
  });

  it("wires tab reorder to reorderPages via HorizontalTabStrip", () => {
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "p-a",
          url: "https://a.example",
          title: "A",
          conversationId: "conv-1",
        },
        {
          id: "p-b",
          url: "https://b.example",
          title: "B",
          conversationId: "conv-1",
        },
      ],
      activePageId: "p-a",
    });
    const reorderSpy = vi.spyOn(
      useBrowserSessionsStore.getState(),
      "reorderPages",
    );
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(
      screen.getByRole("navigation", { name: "浏览器标签页" }),
    ).toBeTruthy();

    const tabA = document.querySelector('[data-tab-id="p-a"]') as HTMLElement;
    const tabB = document.querySelector('[data-tab-id="p-b"]') as HTMLElement;
    expect(tabA).toBeTruthy();
    expect(tabB).toBeTruthy();

    // jsdom 无 elementsFromPoint；拖排序 hit-test 需补桩。
    document.elementsFromPoint = vi.fn().mockReturnValue([tabB]);
    const dx = TAB_DRAG_THRESHOLD_PX + 4;
    fireEvent.pointerDown(tabA, {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerMove(tabA, {
      clientX: 10 + dx,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerUp(tabA, { pointerId: 1, clientX: 10 + dx, clientY: 10 });

    expect(reorderSpy).toHaveBeenCalledWith("conv-1", ["p-b", "p-a"]);
    reorderSpy.mockRestore();
  });

  it("disables open-external when blank and exposes address-bar context menu", () => {
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "p-blank",
          url: "",
          title: "新标签页",
          conversationId: "conv-1",
        },
      ],
      activePageId: "p-blank",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);

    const openBtn = screen.getByLabelText(
      "在系统浏览器打开",
    ) as HTMLButtonElement;
    expect(openBtn.disabled).toBe(true);
    // disabled 外包 span，悬停可出原因（SimpleTooltip）
    expect(openBtn.parentElement?.tagName).toBe("SPAN");

    fireEvent.contextMenu(screen.getByLabelText("地址栏"));
    expect(screen.getByText("在系统浏览器打开")).toBeTruthy();
  });

  it("workspace:// 本会话页可外开：走 openWorkspaceInBrowser，不走 openExternal", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    window.fsApi = {
      previewArchive: vi.fn(),
    } as unknown as typeof window.fsApi;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "p-ws",
          url: "workspace://conv.conv-1/site/index.html",
          title: "index.html",
          conversationId: "conv-1",
        },
      ],
      activePageId: "p-ws",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);

    const openBtn = screen.getByLabelText(
      "在系统浏览器打开",
    ) as HTMLButtonElement;
    expect(openBtn.disabled).toBe(false);
    fireEvent.click(openBtn);

    await waitFor(() => {
      expect(openWorkspaceMock).toHaveBeenCalledWith(
        "conv-1",
        "site/index.html",
      );
    });
    expect(api.openExternal).not.toHaveBeenCalled();
  });

  it("跨会话 workspace:// 仍拒；http(s) 仍走 openExternal", async () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    window.fsApi = {
      previewArchive: vi.fn(),
    } as unknown as typeof window.fsApi;

    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "p-other",
          url: "workspace://conv.other-cid/site/index.html",
          title: "other",
          conversationId: "conv-1",
        },
      ],
      activePageId: "p-other",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(
      (screen.getByLabelText("在系统浏览器打开") as HTMLButtonElement).disabled,
    ).toBe(true);

    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "p-http",
          url: "https://example.com/docs",
          title: "docs",
          conversationId: "conv-1",
        },
      ],
      activePageId: "p-http",
    });
    cleanup();
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    const httpBtn = screen.getByLabelText(
      "在系统浏览器打开",
    ) as HTMLButtonElement;
    expect(httpBtn.disabled).toBe(false);
    fireEvent.click(httpBtn);
    await waitFor(() => {
      expect(api.openExternal).toHaveBeenCalledWith({
        url: "https://example.com/docs",
      });
    });
    expect(openWorkspaceMock).not.toHaveBeenCalled();
  });

  it("file:// 与非法 scheme 仍拒，且不调用 openExternal / openWorkspaceInBrowser", () => {
    const api = mockBrowserApi();
    window.browserApi = api;
    window.fsApi = {
      previewArchive: vi.fn(),
    } as unknown as typeof window.fsApi;
    useBrowserSessionsStore.setState({
      pages: [
        {
          id: "p-file",
          url: "file:///tmp/x.html",
          title: "file",
          conversationId: "conv-1",
        },
      ],
      activePageId: "p-file",
    });
    renderPanel(<BrowserPanel conversationId="conv-1" liveAvailable={false} />);
    expect(
      (screen.getByLabelText("在系统浏览器打开") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(api.openExternal).not.toHaveBeenCalled();
    expect(openWorkspaceMock).not.toHaveBeenCalled();
  });
});
