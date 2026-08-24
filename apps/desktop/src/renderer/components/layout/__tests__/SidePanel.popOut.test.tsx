// @vitest-environment jsdom
import { SidePanel } from "@/components/layout/SidePanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useConversationStore } from "@/stores/conversation";
import { WORKSPACE_TAB_ID, useSidePanelStore } from "@/stores/sidePanel";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom 无 ResizeObserver；HorizontalTabStrip 会挂观察器。
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const layoutState = vi.hoisted(() => ({ isNarrow: false }));

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow: layoutState.isNarrow,
    hideChrome: false,
    conversationDrawerOpen: false,
    setConversationDrawerOpen: () => undefined,
  }),
}));

vi.mock("@/components/workspace/WorkspacePanel", () => ({
  WorkspaceMode: () => null,
}));
vi.mock("@/components/workspace/ConversationChangesPanel", () => ({
  ConversationChangesPanel: () => null,
}));
vi.mock("@/components/workspace/BrowserPanel", () => ({
  BrowserPanel: () => null,
}));
vi.mock("@/components/workspace/BrowserLivePanel", () => ({
  useBrowserRegion: () => ({ show: false, conversationId: null }),
}));
vi.mock("@/components/terminal/TerminalPanel", () => ({
  TerminalPanelBody: () => null,
  useTerminalRegion: () => ({ show: false }),
}));
vi.mock("@/components/layout/FileTabSurface", () => ({
  FileTabSurface: () => null,
}));
vi.mock("@/components/chat/detail/RunDetailScroll", () => ({
  RunDetailScroll: () => null,
}));
vi.mock("@/components/chat/Markdown", () => ({
  Markdown: () => null,
}));
vi.mock("@/components/layout/DesktopFloatWindowBridge", () => ({
  closeOsFloatWindowsForTabs: () => undefined,
}));
vi.mock("@/lib/toast", () => ({
  notifyError: () => undefined,
}));

function renderPanel(): void {
  render(
    <TooltipProvider>
      <SidePanel />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  layoutState.isNarrow = false;
  window.__NATIVE__ = undefined;
  useConversationStore.setState({ currentConversationId: "c1" });
  useSidePanelStore.setState({
    open: true,
    width: 400,
    tabs: [],
    activeTabId: WORKSPACE_TAB_ID,
    floats: [],
    focusSurface: { type: "dock" },
    changesOpen: false,
    changesFocusMessageId: null,
    dismissedContexts: new Set(),
    pendingBadge: 0,
  });
});

afterEach(() => {
  cleanup();
  window.__NATIVE__ = undefined;
  layoutState.isNarrow = false;
});

describe("SidePanel pop-out chrome", () => {
  it("shows pop-out on a wide desktop viewport", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: "弹出 工作区" })).toBeTruthy();
  });

  it("hides pop-out on a narrow viewport", () => {
    layoutState.isNarrow = true;
    renderPanel();
    expect(screen.queryByRole("button", { name: "弹出 工作区" })).toBeNull();
  });

  it("hides pop-out when window.__NATIVE__ is set", () => {
    window.__NATIVE__ = true;
    renderPanel();
    expect(screen.queryByRole("button", { name: "弹出 工作区" })).toBeNull();
  });
});

describe("SidePanel 改动 tab（按需 / 可关）", () => {
  it("does not show 改动 until explicitly opened", () => {
    renderPanel();
    expect(screen.queryByRole("button", { name: "改动" })).toBeNull();
    expect(screen.queryByRole("button", { name: "关闭 改动" })).toBeNull();
  });

  it("shows a closable 改动 tab after showChanges", () => {
    useSidePanelStore.getState().showChanges();
    renderPanel();
    expect(screen.getByRole("button", { name: "改动" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭 改动" }));
    expect(useSidePanelStore.getState().changesOpen).toBe(false);
    expect(screen.queryByRole("button", { name: "改动" })).toBeNull();
  });

  it("keeps 改动 open across remount of the same conversation", () => {
    useSidePanelStore.getState().showChanges();
    renderPanel();
    cleanup();
    renderPanel();
    expect(useSidePanelStore.getState().changesOpen).toBe(true);
    expect(screen.getByRole("button", { name: "改动" })).toBeTruthy();
  });

  it("unloads 改动 when the conversation changes", async () => {
    useSidePanelStore.getState().showChanges();
    renderPanel();
    await act(async () => {
      useConversationStore.setState({ currentConversationId: "c2" });
    });
    await waitFor(() => {
      expect(useSidePanelStore.getState().changesOpen).toBe(false);
    });
  });
});
