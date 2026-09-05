// @vitest-environment jsdom
import { SidePanel } from "@/components/layout/SidePanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MID, plan, started } from "@/stores/__tests__/execution/fixtures";
import { useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { WORKSPACE_TAB_ID, useSidePanelStore } from "@/stores/sidePanel";
import { cleanup, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow: false,
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
  RunDetailScroll: ({ runId }: { runId: string }) => (
    <div data-testid={`run-body-${runId}`} />
  ),
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
  window.__NATIVE__ = undefined;
  useConversationStore.setState({ currentConversationId: "c1" });
  useExecutionStore.setState({ byId: {} });
  useExecutionStore.getState().startExecution(plan, MID);
  useExecutionStore.getState().recordFrame(started("agent-1", "run-1"), MID);
  useExecutionStore.getState().recordFrame(started("agent-2", "run-2", 2), MID);
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
});

describe("SidePanel docked run mount", () => {
  it("mounts only the active run tab body", () => {
    renderPanel();
    act(() => {
      const panel = useSidePanelStore.getState();
      panel.showRunDetail(MID, "run-1", "甲");
      panel.showRunDetail(MID, "run-2", "乙");
    });
    expect(screen.queryByTestId("run-body-run-1")).toBeNull();
    expect(screen.getByTestId("run-body-run-2")).toBeTruthy();
  });
});
