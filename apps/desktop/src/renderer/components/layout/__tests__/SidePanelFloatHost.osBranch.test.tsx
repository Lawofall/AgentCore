// @vitest-environment jsdom
import { SidePanelFloatHost } from "@/components/layout/SidePanelFloatHost";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  WORKSPACE_TAB_ID,
  runDetailTabId,
  useSidePanelStore,
} from "@/stores/sidePanel";
import type { FloatWindowApi } from "@shared/float-window-contract";
import { cleanup, render, screen } from "@testing-library/react";
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
  window.floatWindowApi = undefined;
  window.__WEB__ = undefined;
});

beforeEach(() => {
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

describe("SidePanelFloatHost desktop/web branch", () => {
  it("Web / no floatWindowApi keeps in-app FloatingPanelHost", () => {
    const mid = "msg-1";
    const tabId = runDetailTabId(mid, "run-a");
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: tabId,
      title: "研究员",
      messageId: mid,
      runId: "run-a",
    });
    useSidePanelStore.getState().floatTab(tabId);

    render(
      <TooltipProvider>
        <SidePanelFloatHost />
      </TooltipProvider>,
    );
    expect(screen.getByTestId("floating-panel-host")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "研究员" })).toBeTruthy();
  });

  it("desktop with floatWindowApi skips in-app shell and opens OS window", async () => {
    const open = vi.fn(async () => true);
    const api: FloatWindowApi = {
      open,
      dock: vi.fn(async () => undefined),
      destroy: vi.fn(async () => undefined),
      onClosed: vi.fn(() => () => undefined),
    };
    window.floatWindowApi = api;

    const { useConversationStore } = await import("@/stores/conversation");
    useConversationStore.setState({ currentConversationId: "c1" });

    const mid = "msg-1";
    const tabId = runDetailTabId(mid, "run-a");
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: tabId,
      title: "研究员",
      messageId: mid,
      runId: "run-a",
    });
    useSidePanelStore.getState().floatTab(tabId);

    render(
      <TooltipProvider>
        <SidePanelFloatHost />
      </TooltipProvider>,
    );

    expect(screen.queryByTestId("floating-panel-host")).toBeNull();
    await vi.waitFor(() => {
      expect(open).toHaveBeenCalledWith(
        expect.objectContaining({
          tabId,
          conversationId: "c1",
          title: "研究员",
        }),
      );
    });
  });
});
