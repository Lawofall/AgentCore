// @vitest-environment jsdom

import { CLOUD_BRIDGE_HINT } from "@/lib/cloudBridgeHint";
import { DRAFT_KEY, useConversationStore } from "@/stores/conversation";
import type { ConversationRuntime } from "@/stores/conversation";
import { useUIStore } from "@/stores/ui";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CloudBridgeHint } from "../CloudBridgeHint";

const CID = "c-bridge";

function emptyRuntime(
  over: Partial<ConversationRuntime> = {},
): ConversationRuntime {
  return {
    messages: [],
    memoryUpdates: [],
    isGenerating: false,
    turnPhase: "idle",
    abort: null,
    error: null,
    retry: null,
    errorAction: null,
    messageFocus: null,
    hasMoreBefore: false,
    hasMoreAfter: false,
    loadingOlder: false,
    loadingNewer: false,
    pendingTurnWarning: null,
    pendingTraceId: null,
    toolStartedMs: {},
    executionVia: null,
    waitingForWorkspaceLock: false,
    waitingForDeskProvision: false,
    ...over,
  };
}

function open(via: ConversationRuntime["executionVia"], lastId = "a2") {
  useConversationStore.setState({
    currentConversationId: CID,
    byId: {
      [CID]: emptyRuntime({
        executionVia: via,
        messages: [
          {
            id: "u1",
            role: "user",
            content: "hi",
            createdAt: "2026-01-01T00:00:00Z",
            executionId: null,
            isStreaming: false,
          },
          {
            id: "a1",
            role: "assistant",
            content: "old",
            createdAt: "2026-01-01T00:01:00Z",
            executionId: null,
            isStreaming: false,
          },
          {
            id: lastId,
            role: "assistant",
            content: "latest",
            createdAt: "2026-01-01T00:02:00Z",
            executionId: null,
            isStreaming: false,
          },
        ],
      }),
    },
  });
}

beforeEach(() => {
  useUIStore.setState({ sidecarPreference: "unset" });
  useConversationStore.setState({
    currentConversationId: null,
    byId: { [DRAFT_KEY]: emptyRuntime() },
  });
});

afterEach(cleanup);

describe("CloudBridgeHint", () => {
  it("cloud_bridge + 未强制关 → 只在最新助手泡脚注", () => {
    open("cloud_bridge");
    const { unmount } = render(<CloudBridgeHint messageId="a2" />);
    const hint = screen.getByTestId("cloud-bridge-hint");
    expect(hint.textContent).toBe(CLOUD_BRIDGE_HINT);
    expect(hint.getAttribute("aria-live")).toBe("polite");
    unmount();
    render(<CloudBridgeHint messageId="a1" />);
    expect(screen.queryByTestId("cloud-bridge-hint")).toBeNull();
  });

  it("流式中不展示「完成」", () => {
    open("cloud_bridge");
    act(() => {
      const rt = useConversationStore.getState().byId[CID];
      if (!rt) throw new Error("missing runtime");
      useConversationStore.setState({
        byId: {
          [CID]: {
            ...rt,
            messages: rt.messages.map((m) =>
              m.id === "a2" ? { ...m, isStreaming: true } : m,
            ),
          },
        },
      });
    });
    render(<CloudBridgeHint messageId="a2" />);
    expect(screen.queryByTestId("cloud-bridge-hint")).toBeNull();
  });

  it("显式强制关 → 不展示（勿吓大众）", () => {
    useUIStore.setState({ sidecarPreference: "off" });
    open("cloud_bridge");
    render(<CloudBridgeHint messageId="a2" />);
    expect(screen.queryByTestId("cloud-bridge-hint")).toBeNull();
  });

  it("sidecar / null → 不展示", () => {
    open("sidecar");
    const { rerender } = render(<CloudBridgeHint messageId="a2" />);
    expect(screen.queryByTestId("cloud-bridge-hint")).toBeNull();
    act(() => {
      open(null);
    });
    rerender(<CloudBridgeHint messageId="a2" />);
    expect(screen.queryByTestId("cloud-bridge-hint")).toBeNull();
  });
});
