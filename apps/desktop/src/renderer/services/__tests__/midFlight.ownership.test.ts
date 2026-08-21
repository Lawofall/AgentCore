import { notifyInfo } from "@/lib/toast";
import { dispatchSSEEvent, flushPendingContent } from "@/services/sse/dispatch";
import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { resetQueuedTurnLocalForTests } from "@/services/turns/queuedTurnLocal";
import {
  claimPrimaryStream,
  releasePrimaryStream,
  resetStreamOwnershipForTests,
} from "@/services/turns/streamOwnership";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import type { SSEEvent } from "@/types/events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

const notifyInfoMock = vi.mocked(notifyInfo);
const CID = "conv-mf-race";

/** 可控 SSE 体：测试里按帧 push，模拟双连接时序。 */
function controllableSse(): {
  response: Response;
  push: (event: SSEEvent) => void;
  close: () => void;
  error: (err?: Error) => void;
} {
  const enc = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    push(event) {
      controller.enqueue(enc.encode(`data: ${JSON.stringify(event)}\n\n`));
    },
    close() {
      controller.close();
    },
    error(err = new DOMException("Aborted", "AbortError")) {
      controller.error(err);
    },
  };
}

function ev(
  type: SSEEvent["type"],
  payload: Record<string, unknown> = {},
): SSEEvent {
  return { type, timestamp: "", payload } as SSEEvent;
}

beforeEach(() => {
  vi.clearAllMocks();
  resetStreamOwnershipForTests();
  resetQueuedTurnLocalForTests();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useQueuedTurnsStore.setState({ byConversation: {} });
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().setTurnPhase("streaming", CID);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetStreamOwnershipForTests();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useQueuedTurnsStore.setState({ byConversation: {} });
});

describe("midFlight · 主路门 + store 断言", () => {
  it("经典排队：turn_queued 无用户泡仅条；ack 即 resolve；started 后插泡", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const conv = useConversationStore.getState();
    conv.addMessage(
      {
        id: "u1",
        role: "user",
        content: "第一问",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      },
      CID,
    );
    conv.createAssistantMessage(CID);
    conv.appendToLastMessage("turn1-正文", CID);
    conv.setServerMessageIdOnLastMessage("srv-turn1", CID);

    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "第二问", undefined, "queue");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });
    const body = JSON.parse(
      String(vi.mocked(fetch).mock.calls[0]?.[1]?.body ?? "{}"),
    ) as { delivery?: string };
    expect(body.delivery).toBe("queue");

    sse.push(
      ev("turn_queued", {
        queue_id: "q1",
        position: 1,
        queue_depth: 1,
        conversation_id: CID,
      }),
    );
    await vi.waitFor(() => {
      expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    });
    expect(notifyInfoMock).not.toHaveBeenCalled();
    // queued 后无用户泡；仅 QueuedTurnsBar
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "第二问",
      ),
    ).toBe(false);
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    expect(useQueuedTurnsStore.getState().list(CID)[0]?.content).toBe("第二问");
    // ack 后 composer 可清（Promise 已 settle）
    await expect(pending).resolves.toMatchObject({
      kind: "queued",
      position: 1,
      queueId: "q1",
    });

    // drain 边界交错：conn2 已到 turn_queue_started + message_start，但 turn1 主路未释放 → 缓冲
    sse.push(
      ev("turn_queue_started", {
        queue_id: "q1",
        conversation_id: CID,
        remaining_depth: 0,
        content: "第二问",
      }),
    );
    sse.push(ev("message_start", { message_id: "srv-turn2" }));
    await new Promise((r) => setTimeout(r, 10));

    // 缓冲中：轻态仍在（尚未 flush）；用户泡仍未进时间线
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "第二问",
      ),
    ).toBe(false);

    // 不变式 c：turn1 正文未被 resetAssistant 清掉
    const midRace = getRuntime(CID).messages;
    const turn1Assistant = midRace.find(
      (m) => m.role === "assistant" && m.serverMessageId === "srv-turn1",
    );
    expect(turn1Assistant?.content).toBe("turn1-正文");
    expect(midRace.some((m) => m.serverMessageId === "srv-turn2")).toBe(false);

    // turn1 收口帧仍走 conn1 dispatch（不丢）
    handleMessageStreamEvent(
      ev("message_end", {
        finish_reason: "end_turn",
        cost: {
          input: 1,
          cached: 0,
          output: 1,
          total: 2,
          currency: "USD",
          pricing_source: "curated",
        },
      }),
      { conversationId: CID, source: "server" },
    );
    expect(
      getRuntime(CID).messages.find((m) => m.serverMessageId === "srv-turn1")
        ?.content,
    ).toBe("turn1-正文");

    releasePrimaryStream(CID, turn1Token);
    await vi.waitFor(() => {
      expect(
        getRuntime(CID).messages.some((m) => m.serverMessageId === "srv-turn2"),
      ).toBe(true);
    });

    // 放行后：turn_queue_started 插用户泡并清轻态
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    const after = getRuntime(CID).messages;
    const turn2 = after.find(
      (m) => m.role === "assistant" && m.serverMessageId === "srv-turn2",
    );
    expect(turn2).toBeTruthy();
    expect(after.find((m) => m.serverMessageId === "srv-turn1")?.content).toBe(
      "turn1-正文",
    );
    expect(after.some((m) => m.role === "user" && m.content === "第二问")).toBe(
      true,
    );

    sse.push(ev("content_delta", { delta: "turn2-答" }));
    sse.push(ev("message_end", { finish_reason: "end_turn" }));
    sse.close();
    await vi.waitFor(() => {
      flushPendingContent(CID);
      expect(
        getRuntime(CID).messages.find((m) => m.serverMessageId === "srv-turn2")
          ?.content,
      ).toContain("turn2");
    });
  });

  it("缓冲期空快照清条后 flush 仍插泡", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const conv = useConversationStore.getState();
    conv.addMessage(
      {
        id: "u1",
        role: "user",
        content: "第一问",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      },
      CID,
    );
    conv.createAssistantMessage(CID);
    conv.appendToLastMessage("turn1-正文", CID);
    conv.setServerMessageIdOnLastMessage("srv-turn1", CID);

    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "出队正文", undefined, "queue");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });
    sse.push(
      ev("turn_queued", {
        queue_id: "q-snap",
        position: 1,
        queue_depth: 1,
        conversation_id: CID,
      }),
    );
    await expect(pending).resolves.toMatchObject({
      kind: "queued",
      queueId: "q-snap",
    });

    sse.push(
      ev("turn_queue_started", {
        queue_id: "q-snap",
        conversation_id: CID,
        remaining_depth: 0,
        content: "出队正文",
      }),
    );
    sse.push(ev("message_start", { message_id: "srv-turn2" }));
    await new Promise((r) => setTimeout(r, 10));
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    useQueuedTurnsStore.getState().replaceConversation(CID, []);
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "出队正文",
      ),
    ).toBe(false);

    handleMessageStreamEvent(ev("message_end", { finish_reason: "end_turn" }), {
      conversationId: CID,
      source: "server",
    });
    releasePrimaryStream(CID, turn1Token);
    await vi.waitFor(() => {
      expect(
        getRuntime(CID).messages.some(
          (m) => m.role === "user" && m.content === "出队正文",
        ),
      ).toBe(true);
    });
    sse.close();
  });

  it("排队等待中 Abort：丢缓冲；保留排队条、无用户泡（Stop≠取消）", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const parentAc = new AbortController();
    useConversationStore.getState().setAbort(parentAc, CID);
    useConversationStore.getState().createAssistantMessage(CID);

    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "排队后停止", undefined, "queue");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });

    sse.push(
      ev("turn_queued", {
        queue_id: "q1",
        position: 1,
        queue_depth: 1,
        conversation_id: CID,
      }),
    );
    await expect(pending).resolves.toMatchObject({ kind: "queued" });
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "排队后停止",
      ),
    ).toBe(false);
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);

    sse.push(ev("message_start", { message_id: "srv-should-not-land" }));
    await new Promise((r) => setTimeout(r, 10));

    // 停止 turn1 → 联动 abort midFlight；error 流使泵跳出（mock fetch 不绑 signal）
    parentAc.abort();
    sse.error(new DOMException("Aborted", "AbortError"));

    await new Promise((r) => setTimeout(r, 10));
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "排队后停止",
      ),
    ).toBe(false);
    // Abort 丢缓冲：条保留（Stop ≠ 取消排队；亦未收到 turn_queue_started）
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    expect(
      getRuntime(CID).messages.some(
        (m) => m.serverMessageId === "srv-should-not-land",
      ),
    ).toBe(false);

    releasePrimaryStream(CID, turn1Token);
  });

  it("release 与 abort 同刻：waiter 同步 flush 须丢缓冲，不得 fold message_start", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const parentAc = new AbortController();
    useConversationStore.getState().setAbort(parentAc, CID);
    useConversationStore.getState().createAssistantMessage(CID);

    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "同刻停止", undefined, "queue");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });

    sse.push(
      ev("turn_queued", {
        queue_id: "q1",
        position: 1,
        queue_depth: 1,
        conversation_id: CID,
      }),
    );
    await expect(pending).resolves.toMatchObject({ kind: "queued" });
    // turn_queued 后无用户泡
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "同刻停止",
      ),
    ).toBe(false);
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    sse.push(ev("message_start", { message_id: "srv-race-abort-flush" }));
    await new Promise((r) => setTimeout(r, 10));

    // 关键缝：abort 已置位，紧接着 turn1 finally 同步 release → waiter 同步唤 flush
    parentAc.abort();
    releasePrimaryStream(CID, turn1Token);
    expect(
      getRuntime(CID).messages.some(
        (m) => m.serverMessageId === "srv-race-abort-flush",
      ),
    ).toBe(false);

    sse.error(new DOMException("Aborted", "AbortError"));
    await new Promise((r) => setTimeout(r, 10));
    // 仍无泡；message_start 未 fold；条保留
    expect(
      getRuntime(CID).messages.some(
        (m) => m.role === "user" && m.content === "同刻停止",
      ),
    ).toBe(false);
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
    expect(
      getRuntime(CID).messages.some(
        (m) => m.serverMessageId === "srv-race-abort-flush",
      ),
    ).toBe(false);
  });

  it("协调插话：user_interjection 即时 dispatch，不经主路缓冲", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "插一句", undefined, "steer");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });
    const body = JSON.parse(
      String(vi.mocked(fetch).mock.calls[0]?.[1]?.body ?? "{}"),
    ) as { delivery?: string };
    expect(body.delivery).toBe("steer");

    sse.push(
      ev("user_interjection", {
        interjection_id: "ij1",
        execution_id: "ex1",
        content: "插一句",
        status: "received",
      }),
    );
    sse.close();

    await expect(pending).resolves.toEqual({
      kind: "received",
      interjectionId: "ij1",
    });
    // 主路仍持有也不妨碍插话短流收口
    expect(getRuntime(CID).messages.some((m) => m.content === "插一句")).toBe(
      false,
    );
    releasePrimaryStream(CID, turn1Token);
  });

  it("经典 soft-insert：user_interjection(received) 即时 dispatch，不插 Message 气泡", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "改成中文", undefined, "steer");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });
    const body = JSON.parse(
      String(vi.mocked(fetch).mock.calls[0]?.[1]?.body ?? "{}"),
    ) as { delivery?: string };
    expect(body.delivery).toBe("steer");

    sse.push(
      ev("user_interjection", {
        interjection_id: "inj-steer-1",
        execution_id: "ex1",
        content: "改成中文",
        status: "received",
      }),
    );
    sse.close();

    await expect(pending).resolves.toEqual({
      kind: "received",
      interjectionId: "inj-steer-1",
    });
    // 持久气泡走 execution.userInterjections，不插 conversation Message 行；无瞬态 toast。
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(getRuntime(CID).messages.some((m) => m.content === "改成中文")).toBe(
      false,
    );
    releasePrimaryStream(CID, turn1Token);
  });

  it("经典 soft-insert：仅 status=received 才 ack（injected 单独不 settle）", async () => {
    const turn1Token = claimPrimaryStream(CID);
    const sse = controllableSse();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse.response)),
    );

    const pending = sendMidFlightMessage(CID, "改成中文", undefined, "steer");
    await vi.waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalled();
    });

    sse.push(
      ev("user_interjection", {
        interjection_id: "inj-steer-2",
        execution_id: "ex1",
        content: "改成中文",
        status: "injected",
      }),
    );
    sse.close();

    await expect(pending).resolves.toEqual({ kind: "error" });
    releasePrimaryStream(CID, turn1Token);
  });
});

describe("同连接 turn_queued → turn_queue_started → message_start → 收口（dispatch 全链）", () => {
  it("主路空闲时 turn_queued 后自然续流，正文落在新回合助手气泡", () => {
    // 无 primary = 空闲（idle send 同连接路径）
    const conv = useConversationStore.getState();
    conv.addMessage(
      {
        id: "u-q",
        role: "user",
        content: "排队问",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      },
      CID,
    );
    useQueuedTurnsStore.getState().upsert({
      queueId: "q1",
      conversationId: CID,
      messageId: "u-q",
      content: "排队问",
      position: 1,
      queueDepth: 1,
    });
    conv.createAssistantMessage(CID);

    dispatchSSEEvent(
      ev("turn_queued", {
        queue_id: "q1",
        position: 1,
        queue_depth: 1,
        conversation_id: CID,
      }),
      { conversationId: CID, source: "server" },
    );
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);

    dispatchSSEEvent(
      ev("turn_queue_started", {
        queue_id: "q1",
        conversation_id: CID,
        remaining_depth: 0,
      }),
      { conversationId: CID, source: "server" },
    );
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);

    dispatchSSEEvent(ev("message_start", { message_id: "m-q" }), {
      conversationId: CID,
      source: "server",
    });
    dispatchSSEEvent(ev("content_delta", { delta: "续流正文" }), {
      conversationId: CID,
      source: "server",
    });
    flushPendingContent(CID);
    dispatchSSEEvent(
      ev("message_end", {
        finish_reason: "end_turn",
        cost: {
          input: 10,
          cached: 0,
          output: 5,
          total: 15,
          currency: "USD",
          pricing_source: "curated",
        },
      }),
      { conversationId: CID, source: "server" },
    );

    const last = getRuntime(CID).messages.at(-1);
    expect(last?.role).toBe("assistant");
    expect(last?.serverMessageId).toBe("m-q");
    expect(last?.content).toBe("续流正文");
    expect(last?.isStreaming).toBe(false);
    expect(last?.cost?.total).toBe(15);
    expect(getRuntime(CID).isGenerating).toBe(false);
    expect(
      getRuntime(CID).messages.some((m) => m.id === "u-q" && m.role === "user"),
    ).toBe(true);
  });
});
