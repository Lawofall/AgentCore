import { purgeConversationRuntimeState } from "@/lib/purgeConversationRuntimeState";
import { useBackgroundTasksStore } from "@/stores/backgroundTasks";
import { useBrowserSessionsStore } from "@/stores/browserSessions";
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import { beforeEach, describe, expect, it, vi } from "vitest";

const CID = "conv-del";
const OTHER = "conv-keep";

function resume(conversationId: string, checkpointId: string): PendingResume {
  return {
    messageId: `msg-${checkpointId}`,
    conversationId,
    checkpointId,
    kind: "ask_user",
    userMessage: "q",
    userMessageId: "u1",
    steps: [],
    pending: [],
    workers: [],
    tools: [],
    primitive: "delegate",
    motion: "",
    form: "",
    sides: [],
    maxRounds: 0,
    thorough: true,
    question: "?",
    assumptions: [],
    questions: [],
    intent: "decision",
    origin: "server",
  };
}

beforeEach(() => {
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useQueuedTurnsStore.setState({ byConversation: {} });
  useBackgroundTasksStore.setState({
    byConversation: {},
    modeByConversation: {},
    rootIdByConversation: {},
  });
  useBrowserSessionsStore.setState({ pages: [], activePageId: null });
  vi.unstubAllGlobals();
});

describe("purgeConversationRuntimeState", () => {
  it("删会话清空 pausedTurns / interactions（及 backgroundTasks）", () => {
    usePausedTurnStore.getState().addLiveResume(resume(CID, "cp-1"));
    usePausedTurnStore.getState().addLiveResume(resume(OTHER, "cp-2"));
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m1",
      payload: { checkpoint_id: "cp-ix-1", question: "q" },
    });
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: OTHER,
      messageId: "m2",
      payload: { checkpoint_id: "cp-ix-2", question: "q2" },
    });
    useBackgroundTasksStore.setState({
      byConversation: {
        [CID]: [],
        [OTHER]: [],
      },
      modeByConversation: { [CID]: "local", [OTHER]: "cloud" },
      rootIdByConversation: { [CID]: "root-1", [OTHER]: null },
    });

    purgeConversationRuntimeState(CID);

    expect(
      usePausedTurnStore.getState().pending.map((p) => p.conversationId),
    ).toEqual([OTHER]);
    expect(useInteractionStore.getState().listForConversation(CID)).toEqual([]);
    expect(
      useInteractionStore.getState().listForConversation(OTHER),
    ).toHaveLength(1);
    expect(
      useBackgroundTasksStore.getState().byConversation[CID],
    ).toBeUndefined();
    expect(useBackgroundTasksStore.getState().modeByConversation[OTHER]).toBe(
      "cloud",
    );
  });

  it("清 browserSessions 并调用 browserApi.closeConversation", () => {
    const closeConversation = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("window", {
      browserApi: { closeConversation },
    });

    useBrowserSessionsStore.getState().createPage({
      conversationId: CID,
      title: "A",
    });
    useBrowserSessionsStore.getState().createPage({
      conversationId: OTHER,
      title: "B",
    });

    purgeConversationRuntimeState(CID);

    expect(useBrowserSessionsStore.getState().pagesFor(CID)).toEqual([]);
    expect(useBrowserSessionsStore.getState().pagesFor(OTHER)).toHaveLength(1);
    expect(closeConversation).toHaveBeenCalledWith({ conversationId: CID });
  });

  it("清 queuedTurns FIFO 轻态", () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-del",
      conversationId: CID,
      content: "bye",
      position: 1,
      queueDepth: 1,
    });
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-keep",
      conversationId: OTHER,
      content: "keep",
      position: 1,
      queueDepth: 1,
    });

    purgeConversationRuntimeState(CID);

    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    expect(useQueuedTurnsStore.getState().list(OTHER)).toHaveLength(1);
  });
});
