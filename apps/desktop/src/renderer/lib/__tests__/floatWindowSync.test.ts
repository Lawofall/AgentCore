// @vitest-environment jsdom
import {
  applyFloatProjectionSnapshot,
  buildFloatProjectionSnapshot,
  isFloatSyncMessage,
  isFloatSyncSupported,
  openFloatSyncChannel,
} from "@/lib/floatWindowSync";
import { useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { useInteractionStore } from "@/stores/interactions";
import {
  WORKSPACE_TAB_ID,
  runDetailTabId,
  useSidePanelStore,
} from "@/stores/sidePanel";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
    sliceLruOrder: [],
    pendingFocus: null,
  });
  useExecutionStore.setState({ byId: {} });
  useInteractionStore.setState({ byId: new Map() });
});

describe("floatWindowSync", () => {
  it("buildFloatProjectionSnapshot captures run tab + execution + messages", () => {
    const mid = "msg-1";
    const tabId = runDetailTabId(mid, "run-a");
    useConversationStore.setState({
      currentConversationId: "c1",
      byId: {
        c1: {
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
              id: mid,
              role: "assistant",
              content: "ok",
              createdAt: "2026-01-01T00:00:01Z",
              executionId: null,
              isStreaming: false,
            },
          ],
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
        },
      },
    });
    useSidePanelStore.getState().openTab({
      kind: "run",
      id: tabId,
      title: "Worker",
      messageId: mid,
      runId: "run-a",
    });
    useExecutionStore.setState({
      byId: {
        [mid]: {
          plan: null,
          frames: [],
          playhead: null,
          status: "running",
          debate: null,
          debateRounds: [],
          crossExamEnabled: false,
          debateOpening: null,
          debatePretrial: null,
          evidenceLedger: [],
          workerToolPhases: {},
          runProcesses: null,
          teamSynthesisPreview: null,
          coordinationWait: null,
          executionDetached: null,
          deliveryStatus: null,
          userInterjections: [],
          attestedOutcome: null,
        },
      },
    });

    const snap = buildFloatProjectionSnapshot("c1", tabId);
    expect(snap.tabId).toBe(tabId);
    expect(snap.tabs.some((t) => t.id === tabId)).toBe(true);
    expect(snap.messages).toHaveLength(2);
    expect(snap.executions[mid]?.status).toBe("running");
  });

  it("applyFloatProjectionSnapshot hydrates float-window stores", () => {
    const tabId = runDetailTabId("msg-1", "run-a");
    applyFloatProjectionSnapshot({
      conversationId: "c1",
      tabId,
      tabs: [
        {
          kind: "run",
          id: tabId,
          title: "Worker",
          messageId: "msg-1",
          runId: "run-a",
        },
      ],
      changesFocusMessageId: null,
      messages: [
        {
          id: "msg-1",
          role: "assistant",
          content: "stream",
          createdAt: "2026-01-01T00:00:00Z",
          executionId: null,
          isStreaming: false,
        },
      ],
      executions: {
        "msg-1": {
          plan: null,
          frames: [],
          playhead: null,
          status: "completed",
          debate: null,
          debateRounds: [],
          crossExamEnabled: false,
          debateOpening: null,
          debatePretrial: null,
          evidenceLedger: [],
          workerToolPhases: {},
          runProcesses: null,
          teamSynthesisPreview: null,
          coordinationWait: null,
          executionDetached: null,
          deliveryStatus: null,
          userInterjections: [],
          attestedOutcome: null,
        },
      },
      interactions: [
        {
          id: "appr-1",
          kind: "approval",
          status: "pending",
          conversationId: "c1",
          messageId: "msg-1",
          payload: { approval_id: "appr-1" },
        },
      ],
    });

    expect(useConversationStore.getState().currentConversationId).toBe("c1");
    expect(useConversationStore.getState().byId.c1.messages[0]?.content).toBe(
      "stream",
    );
    expect(useExecutionStore.getState().byId["msg-1"]?.status).toBe(
      "completed",
    );
    expect(useSidePanelStore.getState().tabs.some((t) => t.id === tabId)).toBe(
      true,
    );
    expect(useInteractionStore.getState().byId.get("appr-1")?.status).toBe(
      "pending",
    );
  });

  it("isFloatSyncMessage guards wire shapes", () => {
    expect(isFloatSyncMessage({ type: "focus", tabId: "t1" })).toBe(true);
    expect(
      isFloatSyncMessage({
        type: "request",
        conversationId: "c1",
        tabId: "t1",
      }),
    ).toBe(true);
    expect(
      isFloatSyncMessage({
        type: "snapshot",
        conversationId: "c1",
        tabId: "t1",
        snapshot: { conversationId: "c1" },
      }),
    ).toBe(true);
    expect(isFloatSyncMessage({ type: "snapshot" })).toBe(false);
    expect(isFloatSyncMessage(null)).toBe(false);
  });

  describe("BroadcastChannel capability", () => {
    const OriginalBC = globalThis.BroadcastChannel;

    afterEach(() => {
      globalThis.BroadcastChannel = OriginalBC;
    });

    it("isFloatSyncSupported / openFloatSyncChannel reflect BC presence", () => {
      expect(isFloatSyncSupported()).toBe(true);
      const ch = openFloatSyncChannel();
      expect(ch).not.toBeNull();
      ch?.close();

      // @ts-expect-error intentional capability probe
      globalThis.BroadcastChannel = undefined;
      expect(isFloatSyncSupported()).toBe(false);
      expect(openFloatSyncChannel()).toBeNull();
    });
  });
});
