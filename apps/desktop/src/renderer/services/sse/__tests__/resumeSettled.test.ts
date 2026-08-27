/**
 * 冷卡「继续」点在一张已被上一次续跑吃掉的帧上（EPHEMERAL `resume_settled`，服务端不再
 * 回 404）。两条产品线一起证：
 *
 * - `turn_status=running` = AI 正在继续 → 卡收掉、**不动回合**，让同连接后面的实时流照常
 *   走完（这次改动的产品目的就是让用户无缝看着它继续）。
 * - 其余取值 = 那次续跑已经结束 → 卡收成结果态（带上决策 / 落定时刻），乐观翻回的流式气泡
 *   当场收口并把持久化的结局读回来，别把用户留在半截回答 + 一个转圈的气泡上。
 */
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import { beforeEach, describe, expect, it, vi } from "vitest";

const loadLatestWindow = vi.fn();
vi.mock("@/services/messages", () => ({
  loadLatestWindow: (...args: unknown[]) => loadLatestWindow(...args),
}));

const CID = "conv-resume-settled";
const MID = "srv-msg-settled";
const IX_ID = "cp-settled-1";

function frame(turnStatus: string, decision = "continue") {
  return {
    type: "resume_settled" as const,
    timestamp: "",
    payload: {
      message_id: MID,
      conversation_id: CID,
      kind: "plan_review",
      checkpoint_id: IX_ID,
      decision,
      decided_at: "2026-08-13T09:30:00.000Z",
      turn_status: turnStatus,
    },
  };
}

function pausedShell(): PendingResume {
  return {
    messageId: MID,
    conversationId: CID,
    checkpointId: IX_ID,
    kind: "plan_review",
    userMessage: "go",
    userMessageId: "u1",
    steps: [],
    pending: [],
    question: "",
    assumptions: [],
    questions: [],
    intent: "decision",
    origin: "server",
  };
}

/** 用户点了「继续」之后的本端状态：卡在提交中，挂起气泡被乐观翻回流式。 */
function seedSubmittedCard(): void {
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "go",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  conv.createAssistantMessage(CID);
  conv.setServerMessageIdOnLastMessage(MID, CID);
  conv.setTurnPhase("streaming", CID);
  usePausedTurnStore.setState({ pending: [pausedShell()] });
  useInteractionStore.getState().upsertRequired({
    kind: "plan_review",
    conversationId: CID,
    messageId: MID,
    payload: { checkpoint_id: IX_ID, steps: [], pending: [] },
  });
  useInteractionStore.getState().beginSubmit(IX_ID);
}

beforeEach(() => {
  vi.clearAllMocks();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useInteractionStore.getState().clear();
  usePausedTurnStore.getState().clear();
  seedSubmittedCard();
});

describe("resume_settled · 帧已被处理过", () => {
  it("running：卡收成结果态，回合一动不动（续跑的流接着走）", () => {
    const handled = handleMessageStreamEvent(frame("running"), {
      conversationId: CID,
      source: "server",
    });
    expect(handled).toBe(true);

    const entry = useInteractionStore.getState().byId.get(IX_ID);
    expect(entry?.status).toBe("resolved");
    expect(entry?.resumeSettled).toEqual({
      decision: "continue",
      decidedAt: "2026-08-13T09:30:00.000Z",
      turnStatus: "running",
    });
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    // 无缝续流：气泡仍在流、窗口不刷新（刷新会把正在写的续跑冲掉）。
    expect(getRuntime(CID).isGenerating).toBe(true);
    expect(loadLatestWindow).not.toHaveBeenCalled();
  });

  it("complete：气泡当场收口，并把真实结局读回来", () => {
    handleMessageStreamEvent(frame("complete"), {
      conversationId: CID,
      source: "server",
    });

    expect(
      useInteractionStore.getState().byId.get(IX_ID)?.resumeSettled?.turnStatus,
    ).toBe("complete");
    expect(getRuntime(CID).isGenerating).toBe(false);
    expect(loadLatestWindow).toHaveBeenCalledWith(CID);
  });

  it("failed 同样只是结局，不复活卡、也不留转圈气泡", () => {
    handleMessageStreamEvent(frame("failed", "stop"), {
      conversationId: CID,
      source: "server",
    });

    const entry = useInteractionStore.getState().byId.get(IX_ID);
    expect(entry?.status).toBe("resolved");
    expect(entry?.resumeSettled?.decision).toBe("stop");
    expect(getRuntime(CID).isGenerating).toBe(false);
  });

  it("认不出的 turn_status 按「不知道」收口，绝不当成还在跑", () => {
    handleMessageStreamEvent(frame("what_is_this"), {
      conversationId: CID,
      source: "server",
    });

    expect(
      useInteractionStore.getState().byId.get(IX_ID)?.resumeSettled?.turnStatus,
    ).toBe("unknown");
    expect(getRuntime(CID).isGenerating).toBe(false);
  });

  it("本端没登记过这张卡（recovery 后冷路）也能收成结果态", () => {
    useInteractionStore.getState().clear();

    handleMessageStreamEvent(frame("complete"), {
      conversationId: CID,
      source: "server",
    });

    const entry = useInteractionStore.getState().byId.get(IX_ID);
    expect(entry?.status).toBe("resolved");
    expect(entry?.kind).toBe("plan_review");
    expect(entry?.conversationId).toBe(CID);
  });

  it("dispatchSSEEvent 消费它（不 assertNever）", () => {
    expect(() =>
      dispatchSSEEvent(frame("complete"), {
        conversationId: CID,
        source: "server",
      }),
    ).not.toThrow();
    expect(useInteractionStore.getState().byId.get(IX_ID)?.status).toBe(
      "resolved",
    );
  });

  it("宿主回合已收口（terminal 门闩）仍放行——否则卡永远钉在提交中", () => {
    useConversationStore.getState().setTurnPhase("completed", CID);

    dispatchSSEEvent(frame("complete"), {
      conversationId: CID,
      source: "server",
    });

    expect(useInteractionStore.getState().byId.get(IX_ID)?.status).toBe(
      "resolved",
    );
  });
});
