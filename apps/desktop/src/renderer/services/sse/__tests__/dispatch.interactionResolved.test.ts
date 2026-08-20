// @vitest-environment jsdom
/**
 * `*_resolved` 收口帧必须穿过 turnPhase 门闩（与 `interaction_orphaned` 同一类漏）。
 *
 * 门闩本来只放行 `*_required`：卡能在 `message_end` 之后的窗里画出来（异步团队 detached
 * 续跑、冷挂起紧挨 `message_end(paused)`），配对的收口帧却被丢在门口。
 *
 * 本端自己拍板不受影响——提交路会先乐观 `markResolved`。挡掉伤的全是**本端没答**的收口：
 * 另一端拍板、CEO 仲裁、按假设推进 / 墙钟超时。后两者压根没有人答、也没有回执可依，这一帧
 * 是唯一的终态来源，丢了卡就永远停在 pending，显示可点、点必失败。
 *
 * 为什么 conformance 抓不到：那套 fold 直接吃事件数组，**没有 turnPhase 门闩**这一层，同一
 * 串事件在裁判里恒绿。要抓只能从 `dispatchSSEEvent` 进——门闩就在它里面。
 */
import { logEvent } from "@/lib/log";
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import {
  beginTurnPreflight,
  enterTurnStreaming,
  useConversationStore,
} from "@/stores/conversation";
import {
  type InteractionEntry,
  useInteractionStore,
} from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

vi.mock("@/services/api", () => ({
  api: { post: vi.fn() },
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
  notifyWarning: vi.fn(),
  notifySuccess: vi.fn(),
}));

const CID = "conv-resolved-gate";
const logEventMock = vi.mocked(logEvent);

function send(type: string, payload: Record<string, unknown>): void {
  dispatchSSEEvent({ type, payload } as never, {
    conversationId: CID,
    source: "server",
  });
}

/** 回合跑到 `message_end`：门闩进 completed，但同一条连接还在推 detached 的帧。 */
function runTurnToCompletion(): void {
  beginTurnPreflight(CID);
  enterTurnStreaming(CID);
  useConversationStore.getState().createAssistantMessage(CID);
  send("message_end", { finish_reason: "end_turn" });
  expect(useConversationStore.getState().byId[CID]?.turnPhase).toBe(
    "completed",
  );
}

function entry(id: string): InteractionEntry | undefined {
  return useInteractionStore.getState().byId.get(id);
}

function expectNothingDropped(): void {
  expect(logEventMock).not.toHaveBeenCalledWith(
    "warn",
    "sse.event_dropped",
    expect.anything(),
  );
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: CID, byId: {} });
  useConversationStore.getState().switchConversation(CID);
  useInteractionStore.getState().clear();
  usePausedTurnStore.getState().clear();
  logEventMock.mockReset();
});

describe("dispatchSSEEvent · *_resolved 收口帧", () => {
  it("另一端拍板的热审批：terminal 窗收到 approval_resolved 即收口（并认出是别处结的）", () => {
    runTurnToCompletion();
    // detached worker 在收尾窗要审批——`*_required` 本就放行，卡是真画出来了。
    send("approval_required", {
      approval_id: "ap_1",
      tool_call_id: "tc_1",
      tool_name: "file_write",
    });
    expect(entry("ap_1")?.status).toBe("pending");

    // 本端一次都没点过；这一下是手机上拍的。
    send("approval_resolved", {
      approval_id: "ap_1",
      tool_call_id: "tc_1",
      decision: "approve",
    });

    expect(entry("ap_1")?.status).toBe("resolved");
    // 「已由另一端处理」收口条的唯一来源——journal 水合被明确排除在外。
    expect(entry("ap_1")?.settledElsewhere).toBe(true);
    expectNothingDropped();
  });

  it("墙钟超时的升级卡：没有人答、没有回执，这一帧是唯一终态来源", () => {
    runTurnToCompletion();
    send("escalation_required", {
      escalation_id: "esc_1",
      run_id: "r1",
      question: "这条路走不通，换 B 方案？",
    });
    expect(entry("esc_1")?.status).toBe("pending");

    send("escalation_resolved", {
      escalation_id: "esc_1",
      run_id: "r1",
      status: "timed_out",
    });

    expect(entry("esc_1")?.status).toBe("resolved");
    // 超时兜底不是人答的：别替用户认领一个他没做过的动作。
    expect(entry("esc_1")?.settledElsewhere).toBeFalsy();
    expectNothingDropped();
  });

  it("冷卡在别处续跑：checkpoint_resolved 同时收卡与清挂起帧（否则点了 404）", () => {
    runTurnToCompletion();
    send("checkpoint_required", {
      checkpoint_id: "ck_1",
      question: "两条路选哪条",
    });
    usePausedTurnStore.getState().addLiveResume({
      messageId: "m_paused",
      conversationId: CID,
      checkpointId: "ck_1",
      kind: "ask_user",
      userMessage: "帮我把这件事办完",
      userMessageId: "u1",
      steps: [],
      pending: [],
    } as unknown as PendingResume);

    send("checkpoint_resolved", { checkpoint_id: "ck_1" });

    expect(entry("ck_1")?.status).toBe("resolved");
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    expectNothingDropped();
  });

  it("stopping 窗（用户按停、后端仍在收尾）同样放行", () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    send("stage_card_required", {
      stage_card_id: "sc_1",
      motion: "要不要就这个结论开个辩论",
      form: "debate",
    });
    useConversationStore.getState().setTurnPhase("stopping", CID);

    send("stage_card_resolved", {
      stage_card_id: "sc_1",
      decision: "start_debate",
    });

    expect(entry("sc_1")?.status).toBe("resolved");
    expectNothingDropped();
  });

  it("放行的只有收口帧：同窗正文突变与非收口帧照旧丢弃", () => {
    runTurnToCompletion();

    send("content_delta", { delta: "迟到正文" });

    for (const eventType of ["content_delta"]) {
      expect(logEventMock).toHaveBeenCalledWith(
        "warn",
        "sse.event_dropped",
        expect.objectContaining({ event_type: eventType }),
      );
    }
  });
});
