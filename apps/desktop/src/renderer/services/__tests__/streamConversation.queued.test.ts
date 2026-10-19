import { useConversationStore } from "@/stores/conversation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { streamConversation } from "../streamConversation";

// 发送即有流：POST …/messages 恒返回 SSE（含 turn_queued → 同连接续流）。
// 旧 HTTP 202 JSON 已退役——非 2xx 走错误路径，2xx 一律进泵。

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

function sseResponse(frames: string[]): Response {
  return new Response(frames.join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("streamConversation — 发送即有流（恒 SSE）", () => {
  it("正常 200 SSE 流消费完即 resolve（无 SendOutcome 分支）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([
            'data: {"type":"message_start","timestamp":"","payload":{"message_id":"m1"}}\n\n',
            'data: {"type":"message_end","timestamp":"","payload":{"finish_reason":"end_turn"}}\n\n',
          ]),
        ),
      ),
    );
    useConversationStore.getState().switchConversation("c1");
    useConversationStore.getState().createAssistantMessage("c1");
    useConversationStore.getState().setTurnPhase("streaming", "c1");

    await expect(
      streamConversation({
        conversationId: "c1",
        content: "hi",
        delivery: "steer",
      }),
    ).resolves.toBeUndefined();
  });

  it("turn_queued 帧进泵（不因「排队」短路为 202 JSON）", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        sseResponse([
          'data: {"type":"turn_queued","timestamp":"","payload":{"queue_id":"q1","position":1,"queue_depth":1,"conversation_id":"c1"}}\n\n',
          'data: {"type":"message_start","timestamp":"","payload":{"message_id":"m1"}}\n\n',
          'data: {"type":"message_end","timestamp":"","payload":{"finish_reason":"end_turn"}}\n\n',
        ]),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    useConversationStore.getState().switchConversation("c1");
    useConversationStore.getState().createAssistantMessage("c1");
    useConversationStore.getState().setTurnPhase("streaming", "c1");

    await streamConversation({
      conversationId: "c1",
      content: "queued",
      delivery: "queue",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(
      String(fetchMock.mock.calls[0]?.[1]?.body ?? "{}"),
    ) as { delivery?: string };
    expect(body.delivery).toBe("queue");
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({
          Accept: "text/event-stream",
          "X-Client-Platform": expect.any(String),
          "X-Client-Version": expect.any(String),
        }),
      }),
    );
  });

  it("历史 202 JSON 受理显式失败（契约已退役，不走 queued 分支）", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "queued" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    useConversationStore.getState().switchConversation("c1");
    useConversationStore.getState().setTurnPhase("idle", "c1");

    await expect(
      streamConversation({
        conversationId: "c1",
        content: "hi",
        delivery: "steer",
      }),
    ).rejects.toMatchObject({ kind: "http", status: 202 });
    expect(fetchMock).toHaveBeenCalled();
  });
});
