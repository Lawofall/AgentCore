import {
  type PausedTurnEntry,
  type PendingResume,
  type ResumeOrigin,
  beginPausedSnapshot,
  usePausedTurnStore,
} from "@/stores/pausedTurns";
/**
 * 挂起壳的快照判据（结构化挂起 2b · 多端同权收口面）。
 *
 * 一次 `/recovery` 快照只对**它看得见的**壳有处置权。服务端先落盘挂起帧再发
 * `*_required`，所以「快照发起晚于卡浮现」⇒ 它必然读得到那张帧；这样的空快照回来
 * 就是权威的「帧已被消费」，壳必须清掉，否则用户会点到一张 404 的卡。反过来，快照
 * 在卡浮现前就上了路，回空只说明它太早（live pause 抢跑），不许清。
 */
import type { components } from "@/types/api.generated";
import { beforeEach, describe, expect, it } from "vitest";

type PausedTurnSummary = components["schemas"]["PausedTurnSummary"];

const CID = "conv-paused";

function liveResume(
  messageId: string,
  over: Partial<PendingResume> = {},
): PendingResume {
  return {
    messageId,
    conversationId: CID,
    checkpointId: `cp-${messageId}`,
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
    question: "继续？",
    assumptions: [],
    questions: [],
    intent: "decision",
    origin: "server",
    ...over,
  };
}

function snapshotEntry(
  messageId: string,
  origin: ResumeOrigin = "server",
): PausedTurnEntry {
  return {
    summary: {
      message_id: messageId,
      checkpoint_id: `cp-${messageId}`,
      kind: "ask_user",
      user_message: "q",
      steps: [],
      pending: [],
    } as unknown as PausedTurnSummary,
    origin,
  };
}

const messageIds = (): string[] =>
  usePausedTurnStore
    .getState()
    .pending.filter((p) => p.conversationId === CID)
    .map((p) => p.messageId);

beforeEach(() => {
  usePausedTurnStore.getState().clear();
});

describe("setForConversation 的快照判据", () => {
  it("空快照清掉它发起前就在的壳（另一端已拍板 → 帧被消费）", () => {
    usePausedTurnStore.getState().addLiveResume(liveResume("m-gone"));

    const since = beginPausedSnapshot();
    usePausedTurnStore
      .getState()
      .setForConversation(CID, [], { since, confirmed: ["server"] });

    expect(messageIds()).toEqual([]);
  });

  it("空快照不清它发起后才浮现的壳（live pause 抢跑）", () => {
    const since = beginPausedSnapshot();
    usePausedTurnStore.getState().addLiveResume(liveResume("m-live"));

    usePausedTurnStore
      .getState()
      .setForConversation(CID, [], { since, confirmed: ["server"] });

    expect(messageIds()).toEqual(["m-live"]);
  });

  it("非空快照同样只处置看得见的：期间浮现的另一张卡留住", () => {
    usePausedTurnStore.getState().addLiveResume(liveResume("m-old"));
    const since = beginPausedSnapshot();
    usePausedTurnStore.getState().addLiveResume(liveResume("m-new"));

    usePausedTurnStore
      .getState()
      .setForConversation(CID, [snapshotEntry("m-snap")], {
        since,
        confirmed: ["server"],
      });

    expect(messageIds().sort()).toEqual(["m-new", "m-snap"]);
  });

  it("没问到的来源不清（请求失败 ≠ 帧没了）", () => {
    usePausedTurnStore
      .getState()
      .addLiveResume(liveResume("m-cloud", { origin: "server" }));
    usePausedTurnStore
      .getState()
      .addLiveResume(liveResume("m-local", { origin: "sidecar" }));

    const since = beginPausedSnapshot();
    usePausedTurnStore
      .getState()
      .setForConversation(CID, [], { since, confirmed: ["sidecar"] });

    expect(messageIds()).toEqual(["m-cloud"]);
  });

  it("快照带同一条 message_id → 就地换成快照版本，不留旧壳", () => {
    usePausedTurnStore
      .getState()
      .addLiveResume(liveResume("m-same", { question: "旧文案" }));

    const since = beginPausedSnapshot();
    usePausedTurnStore
      .getState()
      .setForConversation(CID, [snapshotEntry("m-same")], {
        since,
        confirmed: ["server"],
      });

    const entries = usePausedTurnStore.getState().pending;
    expect(entries).toHaveLength(1);
    expect(entries[0]?.checkpointId).toBe("cp-m-same");
  });

  it("别的会话的壳一概不动", () => {
    usePausedTurnStore
      .getState()
      .addLiveResume(liveResume("m-other", { conversationId: "conv-b" }));

    const since = beginPausedSnapshot();
    usePausedTurnStore
      .getState()
      .setForConversation(CID, [], { since, confirmed: ["server"] });

    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
  });

  it("不给观察起点 = 「刚看过」：空快照按权威处置", () => {
    usePausedTurnStore.getState().addLiveResume(liveResume("m-any"));

    usePausedTurnStore.getState().setForConversation(CID, []);

    expect(messageIds()).toEqual([]);
  });
});
