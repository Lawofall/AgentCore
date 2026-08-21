import { afterEach, describe, expect, it } from "vitest";
import { parseTurnQueueStartedUser } from "../queuedTurnBubble";
import {
  __resetQueuedTurnsForTests,
  applyQueuedTurnsSnapshot,
  clearQueuedTurns,
  listQueuedTurns,
  removeQueuedTurn,
  replaceQueuedTurns,
  upsertQueuedTurn,
} from "../queuedTurns";
import {
  QUEUE_DROPPED_HINT,
  __resetReconcileGenerationsForTests,
  reconcileQueuedTurns,
} from "../reconcileQueuedTurns";

afterEach(() => {
  __resetQueuedTurnsForTests();
  __resetReconcileGenerationsForTests();
});

describe("queuedTurns store", () => {
  it("多项按 position 排序，同 queueId upsert 不丢其它项", () => {
    upsertQueuedTurn({
      queueId: "q1",
      conversationId: "c1",
      content: "a",
      position: 1,
      queueDepth: 2,
    });
    upsertQueuedTurn({
      queueId: "q2",
      conversationId: "c1",
      content: "b",
      position: 2,
      queueDepth: 2,
    });
    upsertQueuedTurn({
      queueId: "q1",
      conversationId: "c1",
      content: "a",
      position: 1,
      queueDepth: 3,
    });

    const list = listQueuedTurns("c1");
    expect(list.map((e) => e.queueId)).toEqual(["q1", "q2"]);
    expect(list[0]?.queueDepth).toBe(3);
  });

  it("remove 按 queue_id 只清一项", () => {
    upsertQueuedTurn({
      queueId: "q1",
      conversationId: "c1",
      content: "a",
      position: 1,
      queueDepth: 2,
    });
    upsertQueuedTurn({
      queueId: "q2",
      conversationId: "c1",
      content: "b",
      position: 2,
      queueDepth: 2,
    });
    const hit = removeQueuedTurn("c1", "q1");
    expect(hit?.queueId).toBe("q1");
    expect(listQueuedTurns("c1").map((e) => e.queueId)).toEqual(["q2"]);
  });

  it("turn_queue_started 语义：remove 只清条（出队后再进主时间线用户泡）", () => {
    upsertQueuedTurn({
      queueId: "q-go",
      conversationId: "c1",
      content: "queued then start",
      position: 1,
      queueDepth: 1,
    });
    const hit = removeQueuedTurn("c1", "q-go");
    expect(hit?.content).toBe("queued then start");
    expect(listQueuedTurns("c1")).toEqual([]);
  });

  it("cancel 语义：remove 只清条（排队期无主时间线用户泡可删）", () => {
    upsertQueuedTurn({
      queueId: "q-x",
      conversationId: "c1",
      content: "cancel me",
      position: 1,
      queueDepth: 1,
    });
    removeQueuedTurn("c1", "q-x");
    expect(listQueuedTurns("c1")).toEqual([]);
  });

  it("clearConversation 清空该对话", () => {
    upsertQueuedTurn({
      queueId: "q1",
      conversationId: "c1",
      content: "a",
      position: 1,
      queueDepth: 1,
    });
    upsertQueuedTurn({
      queueId: "q9",
      conversationId: "c2",
      content: "x",
      position: 1,
      queueDepth: 1,
    });
    clearQueuedTurns("c1");
    expect(listQueuedTurns("c1")).toEqual([]);
    expect(listQueuedTurns("c2")).toHaveLength(1);
  });
});

describe("queuedTurns reconcile snapshot", () => {
  it("对账替换本地态：内容/顺序/深度以服务端为准", () => {
    upsertQueuedTurn({
      queueId: "q1",
      conversationId: "c1",
      content: "stale",
      position: 1,
      queueDepth: 1,
    });

    const { droppedLocalIds } = applyQueuedTurnsSnapshot("c1", [
      {
        queueId: "q2",
        content: "from server",
        position: 1,
        interjectionId: null,
      },
      {
        queueId: "q3",
        content: "also server",
        position: 2,
      },
    ]);

    expect(droppedLocalIds).toEqual(["q1"]);
    const list = listQueuedTurns("c1");
    expect(list.map((e) => e.queueId)).toEqual(["q2", "q3"]);
    expect(list[0]).toMatchObject({
      content: "from server",
      position: 1,
      queueDepth: 2,
    });
    expect(list[1]?.queueDepth).toBe(2);
  });

  it("插话来源项：interjectionId 映射进条", () => {
    applyQueuedTurnsSnapshot("c1", [
      {
        queueId: "q-inj",
        content: "promoted from steer",
        position: 1,
        interjectionId: "inj-42",
      },
    ]);
    const list = listQueuedTurns("c1");
    expect(list).toHaveLength(1);
    expect(list[0]?.interjectionId).toBe("inj-42");
  });

  it("服务端已空：清掉本地幽灵项并回报 droppedLocalIds", () => {
    upsertQueuedTurn({
      queueId: "ghost-1",
      conversationId: "c1",
      content: "was queued",
      position: 1,
      queueDepth: 2,
    });
    upsertQueuedTurn({
      queueId: "ghost-2",
      conversationId: "c1",
      content: "also gone",
      position: 2,
      queueDepth: 2,
    });

    const { droppedLocalIds } = applyQueuedTurnsSnapshot("c1", []);
    expect(droppedLocalIds).toEqual(["ghost-1", "ghost-2"]);
    expect(listQueuedTurns("c1")).toEqual([]);
  });

  it("同 queue_id 对账保留本地 degradedFrom", () => {
    upsertQueuedTurn({
      queueId: "q1",
      conversationId: "c1",
      content: "old",
      position: 1,
      queueDepth: 1,
      degradedFrom: "steer",
    });
    applyQueuedTurnsSnapshot("c1", [
      { queueId: "q1", content: "fresh", position: 1 },
    ]);
    expect(listQueuedTurns("c1")[0]).toMatchObject({
      content: "fresh",
      degradedFrom: "steer",
    });
  });

  it("replaceQueuedTurns 整表写入", () => {
    replaceQueuedTurns("c1", [
      {
        queueId: "a",
        conversationId: "c1",
        content: "x",
        position: 2,
        queueDepth: 2,
      },
      {
        queueId: "b",
        conversationId: "c1",
        content: "y",
        position: 1,
        queueDepth: 2,
      },
    ]);
    expect(listQueuedTurns("c1").map((e) => e.queueId)).toEqual(["b", "a"]);
  });
});

describe("reconcileQueuedTurns", () => {
  it("拉取快照后替换本地；服务端空 → dropped + 提示文案常量", async () => {
    upsertQueuedTurn({
      queueId: "local-only",
      conversationId: "c1",
      content: "ghost",
      position: 1,
      queueDepth: 1,
    });
    const result = await reconcileQueuedTurns("c1", async () => []);
    expect(result.failed).toBeUndefined();
    expect(result.droppedLocalIds).toEqual(["local-only"]);
    expect(listQueuedTurns("c1")).toEqual([]);
    expect(QUEUE_DROPPED_HINT).toMatch(/排队项已失效/);
  });

  it("fetch 失败不改本地", async () => {
    upsertQueuedTurn({
      queueId: "keep",
      conversationId: "c1",
      content: "still here",
      position: 1,
      queueDepth: 1,
    });
    const result = await reconcileQueuedTurns("c1", async () => {
      throw new Error("network");
    });
    expect(result.failed).toBe(true);
    expect(result.droppedLocalIds).toEqual([]);
    expect(listQueuedTurns("c1").map((e) => e.queueId)).toEqual(["keep"]);
  });

  it("对账写入插话升格项", async () => {
    const result = await reconcileQueuedTurns("c1", async () => [
      {
        queueId: "q-p",
        content: "from interjection",
        position: 1,
        interjectionId: "inj-9",
      },
    ]);
    expect(result.droppedLocalIds).toEqual([]);
    expect(listQueuedTurns("c1")[0]?.interjectionId).toBe("inj-9");
  });

  // 多端同权后触发源变多（另一端 Queue / 取消 / 出队各来一发）——并发对账不得互相打架。
  it("拉取期间本端新排的项不算失效（快照只对它看见的那一刻负责）", async () => {
    const result = await reconcileQueuedTurns("c1", async () => {
      // GET 在途时用户又发了一条：服务端这份快照里当然没有它。
      upsertQueuedTurn({
        queueId: "q-mine",
        conversationId: "c1",
        content: "刚发的",
        position: 2,
        queueDepth: 2,
      });
      return [{ queueId: "q-other", content: "另一端的", position: 1 }];
    });
    expect(result.droppedLocalIds).toEqual([]);
    expect(listQueuedTurns("c1").map((e) => e.queueId)).toEqual([
      "q-other",
      "q-mine",
    ]);
  });

  it("乱序回来的旧快照不许盖新的（只有最后发起的那次落地）", async () => {
    let releaseFirst: (() => void) | null = null;
    const first = reconcileQueuedTurns(
      "c1",
      () =>
        new Promise((resolve) => {
          releaseFirst = () =>
            resolve([{ queueId: "stale", content: "旧的", position: 1 }]);
        }),
    );
    const second = await reconcileQueuedTurns("c1", async () => [
      { queueId: "fresh", content: "新的", position: 1 },
    ]);
    expect(second.superseded).toBeUndefined();
    (releaseFirst as unknown as () => void)();
    const firstResult = await first;
    expect(firstResult.superseded).toBe(true);
    expect(listQueuedTurns("c1").map((e) => e.queueId)).toEqual(["fresh"]);
  });
});

describe("parseTurnQueueStartedUser", () => {
  it("放宽读 content / 附件 / 点名", () => {
    expect(
      parseTurnQueueStartedUser({
        queue_id: "q1",
        conversation_id: "c1",
        remaining_depth: 0,
        content: "出队正文",
        attachments: [{ name: "a.txt", truncated: true }],
        agent_mentions: [{ agent_id: "w1", role: "研究员" }],
      }),
    ).toEqual({
      queueId: "q1",
      bubble: {
        userText: "出队正文",
        attachments: [{ name: "a.txt", truncated: true }],
        agentMentions: [{ agentId: "w1", role: "研究员" }],
      },
    });
  });

  it("无正文无芯片 → bubble null（仍能凭 queue_id 清条）", () => {
    expect(
      parseTurnQueueStartedUser({
        queue_id: "q-empty",
        conversation_id: "c1",
        remaining_depth: 0,
      }),
    ).toEqual({ queueId: "q-empty", bubble: null });
  });

  it("缺 queue_id → null", () => {
    expect(parseTurnQueueStartedUser({ content: "x" })).toBeNull();
  });
});
