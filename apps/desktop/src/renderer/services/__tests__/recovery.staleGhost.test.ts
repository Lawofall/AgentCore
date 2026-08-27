/**
 * Stale-recovery → false ghost race (方案 A).
 *
 * Open hydrate races loadRecovery ahead of fetchMessageWindow. A cold pause
 * that lands in between leaves ``!cloudLive ∧ pausedCount===0`` while the
 * assistant row is still ``status===running``. settleCloudRunningAssistant
 * must re-fetch before ghosting — same fact-driven refresh as sidecarAttach.
 */
import { useConversationStore } from "@/stores/conversation";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();

vi.mock("@/services/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

vi.mock("@/services/streamConversation", () => ({
  attachConversation: vi.fn(async () => "none"),
  clearLastEventId: vi.fn(),
  // rejoinLiveTurn pulls these; keep inert for the live-refresh path.
}));

vi.mock("@/services/messages", () => ({
  loadLatestWindow: vi.fn(),
}));

import { UNKNOWN_CLOUD_BANNER } from "../turns/helpers";
import {
  markGhostInterrupted,
  resetRejoinLiveTurnForTests,
  settleCloudRunningAssistant,
} from "../turns/recovery";

const CID = "conv-stale-ghost";
const ASSISTANT_ID = "a-running";

const emptyRecovery = {
  sidecarLive: false,
  cloudLive: false,
  cloudKnown: true,
  pausedCount: 0,
  unsynced: [],
};

function seedRunningAssistant(): void {
  const store = useConversationStore.getState();
  store.switchConversation(CID);
  store.addMessage(
    {
      id: "u1",
      role: "user",
      content: "q",
      createdAt: "2026-01-01T00:00:00Z",
      executionId: null,
      isStreaming: false,
    },
    CID,
  );
  store.addMessage(
    {
      id: ASSISTANT_ID,
      role: "assistant",
      content: "partial",
      createdAt: "2026-01-01T00:00:01Z",
      executionId: null,
      isStreaming: true,
      status: "running",
      serverMessageId: ASSISTANT_ID,
    },
    CID,
  );
  store.setGenerating(true, CID);
}

function assistant() {
  return useConversationStore
    .getState()
    .byId[CID].messages.find((m) => m.id === ASSISTANT_ID);
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  usePausedTurnStore.getState().clear();
  apiGet.mockReset();
  vi.unstubAllGlobals();
  // Web path: cloud-only loadRecovery (no sidecar IPC).
  vi.stubGlobal("window", { __WEB__: true });
});

afterEach(() => {
  resetRejoinLiveTurnForTests();
});

describe("settleCloudRunningAssistant (stale recovery race)", () => {
  it("refresh returns paused≥1 → no ghost, pause store hydrated", async () => {
    seedRunningAssistant();
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [
        {
          message_id: ASSISTANT_ID,
          kind: "ask_user",
          checkpoint_id: "cp-race",
          user_message: "q",
          user_message_id: "u1",
          steps: [],
          pending: [],
        },
      ],
      pending_interactions: [],
    });

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
    });

    expect(outcome).toBe("hold");
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith(`/v1/conversations/${CID}/recovery`);
    expect(assistant()?.status).toBe("running");
    expect(assistant()?.finishReason).not.toBe("interrupted");
    expect(assistant()?.isStreaming).toBe(false);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(false);
    const pending = usePausedTurnStore.getState().pending;
    expect(pending).toHaveLength(1);
    expect(pending[0]?.messageId).toBe(ASSISTANT_ID);
    expect(pending[0]?.origin).toBe("server");
  });

  it("refresh still empty → ghost interrupted (dead-lease degrade)", async () => {
    seedRunningAssistant();
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
    });

    expect(outcome).toBe("ghost");
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(assistant()?.status).toBe("incomplete");
    expect(assistant()?.finishReason).toBe("interrupted");
    expect(assistant()?.isStreaming).toBe(false);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(false);
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("stale usage.paused latch resume card is cleared on ghost", async () => {
    seedRunningAssistant();
    // Latch painted a fake「等待确认」card even though recovery has no frame.
    usePausedTurnStore.getState().addLiveResume({
      messageId: ASSISTANT_ID,
      conversationId: CID,
      checkpointId: "cp-stale",
      kind: "ask_user",
      userMessage: "q",
      userMessageId: "u1",
      steps: [],
      pending: [],
      question: "where?",
      assumptions: [],
      questions: [],
      intent: "decision",
      origin: "server",
    });
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);

    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
    });

    expect(outcome).toBe("ghost");
    expect(assistant()?.finishReason).toBe("interrupted");
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("non-empty initial snapshot skips refresh", async () => {
    seedRunningAssistant();
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
      pausedCount: 1,
    });

    expect(outcome).toBe("hold");
    expect(apiGet).not.toHaveBeenCalled();
    expect(assistant()?.status).toBe("running");
    expect(assistant()?.finishReason).not.toBe("interrupted");
    expect(assistant()?.isStreaming).toBe(false);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(false);
  });

  it("cloudKnown=false (refresh still unknown) → hold + unknown banner, never ghost", async () => {
    seedRunningAssistant();
    apiGet.mockRejectedValue(new Error("network down"));

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
      cloudKnown: false,
    });

    expect(outcome).toBe("hold");
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(assistant()?.status).toBe("running");
    expect(assistant()?.finishReason).not.toBe("interrupted");
    expect(assistant()?.isStreaming).toBe(true);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(true);

    const rt = useConversationStore.getState().byId[CID];
    expect(rt.error).toBe(UNKNOWN_CLOUD_BANNER);
    expect(rt.retry).toBeNull();
  });

  it("cloudKnown=false with prior concrete error → keep banner, hold, never overwrite", async () => {
    seedRunningAssistant();
    const concrete = "上游超时，请稍后重试。";
    useConversationStore.getState().setError(concrete, null, CID, null);
    apiGet.mockRejectedValue(new Error("network down"));

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
      cloudKnown: false,
    });

    expect(outcome).toBe("hold");
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(assistant()?.status).toBe("running");
    expect(assistant()?.finishReason).not.toBe("interrupted");
    expect(assistant()?.isStreaming).toBe(true);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(true);

    const rt = useConversationStore.getState().byId[CID];
    expect(rt.error).toBe(concrete);
    expect(rt.error).not.toBe(UNKNOWN_CLOUD_BANNER);
  });

  it("paused latch running assistant with empty recovery → hold, not ghost", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage(
      {
        id: "u1",
        role: "user",
        content: "q",
        createdAt: "2026-01-01T00:00:00Z",
        executionId: null,
        isStreaming: false,
      },
      CID,
    );
    store.addMessage(
      {
        id: ASSISTANT_ID,
        role: "assistant",
        content: "",
        createdAt: "2026-01-01T00:00:01Z",
        executionId: null,
        isStreaming: false,
        status: "running",
        finishReason: "paused",
        serverMessageId: ASSISTANT_ID,
      },
      CID,
    );
    store.setGenerating(true, CID);

    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    const outcome = await settleCloudRunningAssistant(CID, {
      ...emptyRecovery,
    });

    expect(outcome).toBe("hold");
    expect(assistant()?.status).toBe("running");
    expect(assistant()?.finishReason).toBe("paused");
    expect(assistant()?.finishReason).not.toBe("interrupted");
    expect(assistant()?.isStreaming).toBe(false);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(false);
  });
});

describe("markGhostInterrupted (paused latch)", () => {
  it("paused running tail is a no-op", () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage(
      {
        id: ASSISTANT_ID,
        role: "assistant",
        content: "",
        createdAt: "2026-01-01T00:00:01Z",
        executionId: null,
        isStreaming: false,
        status: "running",
        finishReason: "paused",
        serverMessageId: ASSISTANT_ID,
      },
      CID,
    );
    store.setGenerating(true, CID);

    markGhostInterrupted(CID);

    expect(assistant()?.status).toBe("running");
    expect(assistant()?.finishReason).toBe("paused");
    expect(assistant()?.isStreaming).toBe(false);
    expect(useConversationStore.getState().byId[CID].isGenerating).toBe(true);
  });
});
