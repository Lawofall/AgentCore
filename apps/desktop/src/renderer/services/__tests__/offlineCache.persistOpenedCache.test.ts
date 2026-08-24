/**
 * Bugbot 20260806-offline-preview-stale-on-empty-fail — scheme A:
 * empty last assistant must not keep a stale list success preview.
 */
// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const getConversations = vi.fn(
  () => [] as import("@/stores/conversation").Conversation[],
);
vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => getConversations(),
}));

import type { Conversation, Message } from "@/stores/conversation";
import { persistOpenedCache } from "../offlineCache";

const STALE_SUCCESS = "上次成功回复的摘要内容";

function msg(
  id: string,
  role: "user" | "assistant",
  content: string,
  extra: Partial<Message> = {},
): Message {
  return {
    id,
    role,
    content,
    createdAt: "2026-01-01T00:00:00Z",
    executionId: null,
    isStreaming: false,
    ...extra,
  };
}

function listed(id: string, preview: string | null): Conversation {
  return {
    id,
    title: "对话",
    updatedAt: "2026-01-01T00:00:00Z",
    messageCount: 2,
    lastMessagePreview: preview,
    folderId: null,
    localContainerRootId: null,
    localRootId: null,
    pinned: false,
    archived: false,
  };
}

describe("persistOpenedCache preview", () => {
  const putOpenedConversation = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    getConversations.mockReset();
    getConversations.mockReturnValue([]);
    putOpenedConversation.mockClear();
    Object.defineProperty(window, "localStoreApi", {
      configurable: true,
      value: { putOpenedConversation },
    });
  });

  it("does not persist an empty opened window", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);

    await persistOpenedCache("c1", [], [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    expect(putOpenedConversation).not.toHaveBeenCalled();
  });

  it("does not keep stale list preview when last assistant is empty failure", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const messages = [
      msg("u1", "user", "你好"),
      msg("a1", "assistant", "", { finishReason: "error" }),
    ];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    expect(putOpenedConversation).toHaveBeenCalledTimes(1);
    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).toBe(
      "模型调用失败，请重试。",
    );
    expect(payload.conversation.lastMessagePreview).not.toBe(STALE_SUCCESS);
  });

  it("walks back to the previous assistant when empty cancelled (no user / 已停止)", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const messages = [
      msg("a0", "assistant", "先前助手回复"),
      msg("u1", "user", "你好"),
      msg("a1", "assistant", "", {
        finishReason: "cancelled",
      }),
    ];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).toBe("先前助手回复");
    expect(payload.conversation.lastMessagePreview).not.toBe("你好");
    expect(payload.conversation.lastMessagePreview).not.toBe("已停止");
  });

  it("does not use the user sentence when cancelled has no prior assistant", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const messages = [
      msg("u1", "user", "你好"),
      msg("a1", "assistant", "", {
        finishReason: "cancelled",
      }),
    ];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).not.toBe("你好");
    expect(payload.conversation.lastMessagePreview).not.toBe("已停止");
    expect(payload.conversation.lastMessagePreview).toBe(STALE_SUCCESS);
  });

  it("reads finishReason from runs when message.finishReason absent", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const messages = [
      msg("u1", "user", "你好"),
      msg("a1", "assistant", "", {
        runs: {
          events: [],
          finishReason: "unproductive",
        },
      }),
    ];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).toBe(
      "工具连续无有效进展或参数无效，请重试。",
    );
  });

  it("still slices visible message text when present", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const long = "可见正文".repeat(40);
    const messages = [msg("u1", "user", "你好"), msg("a1", "assistant", long)];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).toBe(long.slice(0, 80));
  });

  it("prefers error.message via visibleMessageText over synthetic", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const messages = [
      msg("u1", "user", "你好"),
      msg("a1", "assistant", "", {
        finishReason: "error",
        error: { code: "LLM_ERROR", message: "上游返回了具体错误文案" },
      }),
    ];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).toBe(
      "上游返回了具体错误文案",
    );
  });

  it("clears preview (no stale fallback) when empty but non-failure finish", async () => {
    getConversations.mockReturnValue([listed("c1", STALE_SUCCESS)]);
    const messages = [
      msg("u1", "user", "你好"),
      msg("a1", "assistant", "", { finishReason: "end_turn" }),
    ];

    await persistOpenedCache("c1", messages, [], {
      hasMoreBefore: false,
      hasMoreAfter: false,
    });

    const payload = putOpenedConversation.mock.calls[0][0];
    expect(payload.conversation.lastMessagePreview).toBeNull();
    expect(payload.conversation.lastMessagePreview).not.toBe(STALE_SUCCESS);
  });
});
