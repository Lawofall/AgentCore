// @vitest-environment jsdom
import { SidePanelFloatHost } from "@/components/layout/SidePanelFloatHost";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useConversationStore } from "@/stores/conversation";
import {
  CHANGES_TAB_ID,
  WORKSPACE_TAB_ID,
  runDetailTabId,
  useSidePanelStore,
} from "@/stores/sidePanel";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/layout/SidePanelSurfaceBody", async () => {
  const actual = await vi.importActual<
    typeof import("@/components/layout/SidePanelSurfaceBody")
  >("@/components/layout/SidePanelSurfaceBody");
  return {
    ...actual,
    SidePanelSurfaceBody: ({ tabId }: { tabId: string }) => (
      <div data-testid={`float-body-${tabId}`} />
    ),
  };
});

afterEach(() => {
  cleanup();
});

beforeEach(() => {
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

function renderHost(ui: ReactElement = <SidePanelFloatHost />) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

describe("SidePanelFloatHost", () => {
  it("stays mounted empty when there are no floats", () => {
    renderHost();
    expect(
      screen.getByTestId("floating-panel-host").getAttribute("data-empty"),
    ).toBe("true");
  });

  it("renders floated run tabs from the store and docks on 钉回", () => {
    const mid = "msg-1";
    const tabId = runDetailTabId(mid, "run-a");
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: tabId,
      title: "研究员",
      messageId: mid,
      runId: "run-a",
    });
    expect(useSidePanelStore.getState().floatTab(tabId)).toBe(true);

    renderHost();
    expect(screen.getByRole("dialog", { name: "研究员" })).toBeTruthy();
    expect(screen.getByTestId(`float-body-${tabId}`)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "钉回主坞" }));
    expect(useSidePanelStore.getState().floats).toHaveLength(0);
    expect(useSidePanelStore.getState().activeTabId).toBe(tabId);
    expect(useSidePanelStore.getState().focusSurface).toEqual({ type: "dock" });
  });

  it("shows two floated runs side by side", () => {
    const mid = "msg-1";
    const a = runDetailTabId(mid, "run-a");
    const b = runDetailTabId(mid, "run-b");
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: a,
      title: "Worker A",
      messageId: mid,
      runId: "run-a",
    });
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: b,
      title: "Worker B",
      messageId: mid,
      runId: "run-b",
    });
    useSidePanelStore.getState().floatTab(a);
    useSidePanelStore.getState().floatTab(b);

    renderHost();
    expect(screen.getByRole("dialog", { name: "Worker A" })).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Worker B" })).toBeTruthy();
  });

  it("workspace float has 钉回 but no destroy control", () => {
    useSidePanelStore.getState().floatTab(WORKSPACE_TAB_ID);
    renderHost();
    expect(screen.getByRole("dialog", { name: "工作区" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "钉回主坞" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "关闭浮窗" })).toBeNull();
  });

  it("changes float can be closed via destroy (unload tab)", () => {
    useSidePanelStore.getState().showChanges();
    useSidePanelStore.getState().floatTab(CHANGES_TAB_ID);
    renderHost();
    expect(screen.getByRole("dialog", { name: "改动" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭浮窗" }));
    expect(useSidePanelStore.getState().floats).toHaveLength(0);
    expect(useSidePanelStore.getState().changesOpen).toBe(false);
  });
});
