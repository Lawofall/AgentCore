// @vitest-environment jsdom
import { DesktopFloatWindowBridge } from "@/components/layout/DesktopFloatWindowBridge";
import { useConversationStore } from "@/stores/conversation";
import {
  WORKSPACE_TAB_ID,
  runDetailTabId,
  useSidePanelStore,
} from "@/stores/sidePanel";
import type {
  FloatWindowApi,
  FloatWindowOpenInput,
} from "@shared/float-window-contract";
import { cleanup, render } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
  window.floatWindowApi = undefined;
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
  useConversationStore.setState({ currentConversationId: "c1" });
});

describe("DesktopFloatWindowBridge closed wiring", () => {
  it("onClosed user/dock → dockTab; destroy → destroyFloat", () => {
    let closedCb: Parameters<FloatWindowApi["onClosed"]>[0] | null = null;
    const api: FloatWindowApi = {
      open: vi.fn(async () => true),
      dock: vi.fn(async () => undefined),
      destroy: vi.fn(async () => undefined),
      onClosed: (cb) => {
        closedCb = cb;
        return () => {
          closedCb = null;
        };
      },
    };
    window.floatWindowApi = api;

    const mid = "msg-1";
    const tabId = runDetailTabId(mid, "run-a");
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: tabId,
      title: "Worker",
      messageId: mid,
      runId: "run-a",
    });
    useSidePanelStore.getState().floatTab(tabId);

    render(<DesktopFloatWindowBridge />);
    expect(closedCb).toBeTruthy();

    act(() => {
      closedCb?.({ tabId, reason: "user" });
    });
    expect(useSidePanelStore.getState().floats).toHaveLength(0);
    expect(useSidePanelStore.getState().activeTabId).toBe(tabId);
    expect(useSidePanelStore.getState().tabs.some((t) => t.id === tabId)).toBe(
      true,
    );

    act(() => {
      useSidePanelStore.getState().floatTab(tabId);
      closedCb?.({ tabId, reason: "destroy" });
    });
    expect(useSidePanelStore.getState().tabs.some((t) => t.id === tabId)).toBe(
      false,
    );
  });
});

describe("DesktopFloatWindowBridge open dedupe", () => {
  it("opens each float once; zIndex/focus churn does not re-open peers", async () => {
    const open = vi.fn(async (_input: FloatWindowOpenInput) => true);
    window.floatWindowApi = {
      open,
      dock: vi.fn(async () => undefined),
      destroy: vi.fn(async () => undefined),
      onClosed: () => () => undefined,
    };

    const mid = "msg-1";
    const a = runDetailTabId(mid, "run-a");
    const b = runDetailTabId(mid, "run-b");
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: a,
      title: "A",
      messageId: mid,
      runId: "run-a",
    });
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: b,
      title: "B",
      messageId: mid,
      runId: "run-b",
    });

    render(<DesktopFloatWindowBridge />);

    await act(async () => {
      useSidePanelStore.getState().floatTab(a);
    });
    await act(async () => {
      useSidePanelStore.getState().floatTab(b);
    });

    const opensFor = (tabId: string) =>
      open.mock.calls.filter((c) => c[0]?.tabId === tabId).length;

    // open effect (new) + focusSurface effect (once per focus move) ≤ 2 each.
    expect(opensFor(a)).toBeLessThanOrEqual(2);
    expect(opensFor(b)).toBeLessThanOrEqual(2);

    const afterDual = open.mock.calls.length;
    await act(async () => {
      // Already-focused float: must not bump zIndex / re-open all peers.
      useSidePanelStore.getState().focusFloat(b);
      useSidePanelStore.getState().focusFloat(b);
    });
    expect(open.mock.calls.length).toBe(afterDual);

    // Switching focus to A should open/focus A once more, not A+B.
    await act(async () => {
      useSidePanelStore.getState().focusFloat(a);
    });
    expect(opensFor(b)).toBeLessThanOrEqual(2);
    expect(open.mock.calls.some((c) => c[0]?.tabId === a)).toBe(true);
  });
});
