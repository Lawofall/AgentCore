// @vitest-environment jsdom
/**
 * firehose `ai_attention` → 桌面「某个对话在等你」（云对话多端同权 B2 · P1）。
 *
 * 三条边界：帧真的被消费（P0 落地时这里是 no-op）、`resolved` 撤得掉（谁放行的都算）、
 * 关流即作废（不把上一个账号的提醒留给下一个）。
 */
import {
  applyAiAttention,
  applyAiAttentionSnapshot,
  clearAiAttentionForConversation,
  useAiAttentionStore,
} from "@/stores/aiAttention";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startRealtime, stopRealtime } from "../realtime";

vi.mock("@/stores/messaging", () => ({
  useMessagingStore: {
    getState: () => ({
      activeChatId: null,
      fetchChats: vi.fn(async () => {}),
      fetchFriends: vi.fn(async () => {}),
      fetchFriendRequests: vi.fn(async () => {}),
      loadMessages: vi.fn(async () => {}),
      applyIncoming: vi.fn(),
      applyMessageUpdated: vi.fn(),
      applyPresence: vi.fn(),
      applyFriendRequestEvent: vi.fn(),
    }),
  },
}));

vi.mock("@/hooks/useFolderSharing", () => ({
  invalidateAllFolderSharing: vi.fn(),
}));

const CID = "conv-attention";

/** A pushable SSE body so a test can drive frame-by-frame timing. */
function sseStream(): {
  response: Response;
  push: (chunk: string) => void;
  close: () => void;
} {
  const encoder = new TextEncoder();
  let push!: (chunk: string) => void;
  let close!: () => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      push = (chunk) => {
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch {
          /* already closed */
        }
      };
      close = () => {
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      };
    },
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    push,
    close,
  };
}

function attentionFrame(
  state: "required" | "resolved",
  overrides: Record<string, unknown> = {},
): string {
  return `data: ${JSON.stringify({
    type: "ai_attention",
    state,
    conversation_id: CID,
    turn_id: "turn-1",
    interaction_id: "appr-1",
    kind: "approval",
    title: "需要授权：终端",
    ...overrides,
  })}\n\n`;
}

async function tick(times = 6): Promise<void> {
  for (let i = 0; i < times; i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

function entries() {
  return useAiAttentionStore.getState().entries;
}

beforeEach(() => {
  useAiAttentionStore.setState({ entries: [] });
});

afterEach(() => {
  stopRealtime();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useAiAttentionStore.setState({ entries: [] });
});

describe("firehose ai_attention", () => {
  it("required 落进 store（不再 no-op），resolved 撤掉", async () => {
    const { response, push, close } = sseStream();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(response)),
    );

    startRealtime();
    await tick();

    push(attentionFrame("required"));
    await tick();
    expect(entries()).toEqual([
      {
        interactionId: "appr-1",
        conversationId: CID,
        turnId: "turn-1",
        kind: "approval",
        title: "需要授权：终端",
      },
    ]);

    // 另一端拍板 → 撤掉（谁放行的都算，超时 / 孤儿 / Stop 同样发 resolved）。
    push(attentionFrame("resolved"));
    await tick();
    expect(entries()).toEqual([]);
    close();
  });

  it("同一 interaction 重发只更新文案，不重复占位、不跳序", async () => {
    const { response, push, close } = sseStream();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(response)),
    );

    startRealtime();
    await tick();

    push(attentionFrame("required", { interaction_id: "a", title: "第一张" }));
    push(attentionFrame("required", { interaction_id: "b", title: "第二张" }));
    push(
      attentionFrame("required", { interaction_id: "a", title: "改了标题" }),
    );
    await tick();

    expect(entries().map((e) => [e.interactionId, e.title])).toEqual([
      ["a", "改了标题"],
      ["b", "第二张"],
    ]);
    close();
  });

  it("缺字段的帧直接丢弃，不留半条提醒", async () => {
    const { response, push, close } = sseStream();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(response)),
    );

    startRealtime();
    await tick();

    push(attentionFrame("required", { conversation_id: "" }));
    push(attentionFrame("required", { interaction_id: "" }));
    await tick();

    expect(entries()).toEqual([]);
    close();
  });

  it("关流即清空——提醒不跨账号会话留存", async () => {
    const { response, push, close } = sseStream();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(response)),
    );

    startRealtime();
    await tick();
    push(attentionFrame("required"));
    await tick();
    expect(entries()).toHaveLength(1);

    stopRealtime();
    expect(entries()).toEqual([]);
    close();
  });
});

describe("ai_attention_snapshot replace", () => {
  it("空表 replace 灭假灯；缺字段帧不清表", () => {
    useAiAttentionStore.setState({
      entries: [
        {
          interactionId: "stale",
          conversationId: CID,
          turnId: "t",
          kind: "approval",
          title: "假灯",
        },
      ],
    });
    applyAiAttentionSnapshot({ entries: [] });
    expect(entries()).toEqual([]);

    applyAiAttention({
      type: "ai_attention",
      state: "required",
      conversation_id: CID,
      turn_id: "t",
      interaction_id: "keep",
      kind: "approval",
      title: "真灯",
    });
    applyAiAttentionSnapshot(null);
    applyAiAttentionSnapshot({});
    applyAiAttentionSnapshot({ entries: "nope" });
    expect(entries()).toHaveLength(1);
  });
});

describe("clearConversation 仍可用（打开对话不再走这条）", () => {
  it("只清该会话，别的会话的提醒留着", () => {
    useAiAttentionStore.setState({
      entries: [
        {
          interactionId: "x",
          conversationId: CID,
          turnId: "t",
          kind: "approval",
          title: "这条",
        },
        {
          interactionId: "y",
          conversationId: "other",
          turnId: "t",
          kind: "plan_review",
          title: "别的会话",
        },
      ],
    });

    clearAiAttentionForConversation(CID);

    expect(entries().map((e) => e.conversationId)).toEqual(["other"]);
  });
});
