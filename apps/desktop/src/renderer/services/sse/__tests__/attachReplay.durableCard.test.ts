// @vitest-environment jsdom
/**
 * Attach 回放段 → 耐久卡绑定（SSE-A1 回归）。
 *
 * 桌面 attach 恒带 ``Last-Event-ID``，走后端 journal 游标回放。``message_start`` 不落
 * journal，故回放段由服务端合成一帧盖在段首；没有它，本回合气泡拿不到服务端 message_id，
 * 而「继续」卡正是按这个 id 提交（``POST …/messages/{id}/resume``）——冷绑定会退到
 * **上一回合**的 id（提交 404）或干脆画不出卡。这里从真实入口 `dispatchSSEEvent` 灌一段
 * 停在 ask_user 的回放，钉住绑定与重复 attach 的幂等。
 */
import { listVisibleColdResumes } from "@/services/resume";
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import {
  beginTurnPreflight,
  enterTurnStreaming,
} from "@/stores/conversation/turnPhaseActions";
import { useExecutionStore } from "@/stores/execution";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import type { SSEEvent } from "@/types/events";
import { beforeEach, describe, expect, it } from "vitest";

const CID = "conv-attach-replay";
/** 上一回合已收口的助手行（hydrate 自消息窗，带服务端 id）。 */
const PREV_MID = "srv-turn-prev";
/** 本回合服务端 turn id —— 回放段盖章帧携带、也是 resume 的提交键。 */
const LIVE_MID = "srv-turn-live";

function ev(type: string, payload: Record<string, unknown>): SSEEvent {
  return { type, timestamp: "", payload } as SSEEvent;
}

/** 服务端 `build_cursor_replay` 段形：盖章 → 正文 → 耐久 required → paused 收口。 */
function replaySegment(turnId: string, opts: { stamp: boolean }): SSEEvent[] {
  return [
    ...(opts.stamp
      ? [ev("message_start", { message_id: turnId, conversation_id: CID })]
      : []),
    ev("content_delta", { delta: "我先按 A 方案推进。" }),
    ev("checkpoint_required", {
      checkpoint_id: "cp1",
      conversation_id: CID,
      question: "继续按 A 方案，还是换 B？",
      assumptions: [],
      questions: [],
      intent: "decision",
    }),
    ev("message_end", { finish_reason: "paused" }),
  ];
}

/** 与真实 attach 同前置（beginTurnPreflight → enterTurnStreaming），再逐帧灌回放段。 */
function foldReplay(events: SSEEvent[]): void {
  beginTurnPreflight(CID);
  enterTurnStreaming(CID);
  for (const e of events) {
    dispatchSSEEvent(e, { conversationId: CID, source: "server" });
  }
}

function assistants() {
  return getRuntime(CID).messages.filter((m) => m.role === "assistant");
}

function checkpointMarkers() {
  return (assistants().at(-1)?.process ?? []).filter(
    (s) => s.kind === "checkpoint",
  );
}

/** 真正会被 ResumePrompt 画出来的卡（`messageId` 即提交键）。 */
function pendingCards() {
  return listVisibleColdResumes(CID);
}

/**
 * 重开会话后的 attach 起点：上一回合完整、本回合只有用户行 —— 在飞助手泡由
 * `resetPartialTurnForReplay` 原位清空（保留气泡 id），等回放段重建。
 */
beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
  useInteractionStore.getState().clear();
  usePausedTurnStore.getState().clear();

  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.addMessage({
    id: "u0",
    role: "user",
    content: "上一轮",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  conv.addMessage({
    id: "a0",
    role: "assistant",
    content: "上一轮答复",
    createdAt: "",
    executionId: null,
    isStreaming: false,
    serverMessageId: PREV_MID,
  });
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "这一轮",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
});

describe("attach 游标回放 · 耐久卡绑定", () => {
  it("盖章帧开场 → ask 卡绑本回合服务端 id（可提交，不是上一回合）", () => {
    foldReplay(replaySegment(LIVE_MID, { stamp: true }));

    expect(assistants()).toHaveLength(2);
    expect(assistants().at(-1)?.serverMessageId).toBe(LIVE_MID);

    // 冷绑定键 = 本回合 id：InteractionStore 与画出的卡都必须指向它。
    expect(useInteractionStore.getState().byId.get("cp1")?.messageId).toBe(
      LIVE_MID,
    );
    const cards = pendingCards();
    expect(cards).toHaveLength(1);
    expect(cards[0].messageId).toBe(LIVE_MID);
    expect(cards[0].kind).toBe("ask_user");
    expect(cards[0].question).toBe("继续按 A 方案，还是换 B？");
  });

  it("缺盖章帧（旧服务端）→ 卡绝不会绑到本回合，用户卡死", () => {
    foldReplay(replaySegment(LIVE_MID, { stamp: false }));

    // 兜底不在前端：没有服务端盖章就没有可提交的键，画不出卡或错绑上一回合。
    expect(pendingCards().map((c) => c.messageId)).not.toContain(LIVE_MID);
  });

  it("重复 attach 幂等：不重复建气泡 / 不重复画卡，上一回合不受影响", () => {
    foldReplay(replaySegment(LIVE_MID, { stamp: true }));
    foldReplay(replaySegment(LIVE_MID, { stamp: true }));

    // 同 message_id = 同回合重开：复用原气泡，不另起一条。
    expect(assistants()).toHaveLength(2);
    expect(assistants().at(-1)?.serverMessageId).toBe(LIVE_MID);
    expect(checkpointMarkers()).toHaveLength(1);

    const cards = pendingCards();
    expect(cards).toHaveLength(1);
    expect(cards[0].messageId).toBe(LIVE_MID);

    const prev = assistants()[0];
    expect(prev.serverMessageId).toBe(PREV_MID);
    expect(prev.content).toBe("上一轮答复");
  });

  it("full replay leftover team_preview is skipped (no IX / no stamp)", () => {
    const leftoverReplay = [
      ev("message_start", { message_id: LIVE_MID, conversation_id: CID }),
      ev("content_delta", { delta: "预计 2 人开工" }),
      ev("team_preview_required" as string, {
        checkpoint_id: "tp-settled",
        conversation_id: CID,
        primitive: "delegate",
        workers: [
          { run_id: "r1", role: "研", task: "调研", depends_on: [] },
          { run_id: "r2", role: "写", task: "成文", depends_on: [] },
        ],
        tools: [],
        motion: "",
        form: "",
        sides: [],
        max_rounds: 0,
        thorough: true,
      }),
      ev("message_end", { finish_reason: "paused" }),
      ev("team_preview_resolved" as string, {
        checkpoint_id: "tp-settled",
        decision: "continue",
      }),
      ev("message_end", { finish_reason: "stop" }),
    ];
    foldReplay(leftoverReplay);
    foldReplay(leftoverReplay);

    expect(
      useInteractionStore.getState().byId.get("tp-settled"),
    ).toBeUndefined();
    expect(
      (assistants().at(-1)?.process ?? []).some(
        (s) => (s as { kind: string }).kind === "team_preview",
      ),
    ).toBe(false);
    expect(pendingCards()).toHaveLength(0);
  });
});
