import { overlayIncomingWithRicherExisting } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import type { Message } from "@/stores/conversation";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();
vi.mock("@/services/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

import { jumpToMessage } from "../messages";

const CID = "conv-jump";

function msg(
  id: string,
  content: string,
  extra: Partial<Message> = {},
): Message {
  return {
    id,
    role: "assistant",
    content,
    createdAt: "2026-01-01T00:00:00Z",
    executionId: null,
    isStreaming: false,
    ...extra,
  };
}

function listPayload(messages: Message[]) {
  return {
    data: messages.map((m) => ({
      id: m.id,
      conversation_id: CID,
      role: m.role,
      content: m.content,
      reasoning_content: m.reasoning ?? null,
      created_at: m.createdAt || "2026-01-01T00:00:00Z",
      runs: null,
    })),
    total: messages.length,
    has_more_before: false,
    has_more_after: false,
    memory_updates: [],
  };
}

beforeEach(() => {
  apiGet.mockReset();
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
    pendingFocus: null,
  });
});

describe("jumpToMessage", () => {
  it("focuses by client bubble id when permalink carries serverMessageId", () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage({
      id: "client-uuid",
      serverMessageId: "srv-msg-1",
      role: "assistant",
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });

    void jumpToMessage(CID, "srv-msg-1");

    expect(useConversationStore.getState().byId[CID]?.messageFocus?.id).toBe(
      "client-uuid",
    );
  });

  it("still focuses when the target is already the client id", () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage({
      id: "client-uuid",
      role: "assistant",
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });

    void jumpToMessage(CID, "client-uuid");

    expect(useConversationStore.getState().byId[CID]?.messageFocus?.id).toBe(
      "client-uuid",
    );
  });

  it("does not wipe the adopted window when around returns empty", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage(msg("kept", "already adopted"));
    apiGet.mockResolvedValue(listPayload([]));

    await jumpToMessage(CID, "missing-hit");

    expect(useConversationStore.getState().byId[CID]?.messages).toEqual([
      expect.objectContaining({ id: "kept", content: "already adopted" }),
    ]);
  });

  it("replaces with a historical around slice of different ids", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage(msg("latest", "tail"));
    apiGet.mockResolvedValue(listPayload([msg("old-hit", "history")]));

    await jumpToMessage(CID, "old-hit");

    const ids = (useConversationStore.getState().byId[CID]?.messages ?? []).map(
      (m) => m.id,
    );
    expect(ids).toEqual(["old-hit"]);
  });

  it("keeps a thicker overlapping bubble instead of the thin around copy", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage(
      msg("m1", "thick adopted body that must survive", {
        runs: { events: [{ type: "run_plan" } as never], finishReason: "stop" },
      }),
    );
    apiGet.mockResolvedValue(listPayload([msg("m0", "older"), msg("m1", "")]));

    await jumpToMessage(CID, "m0");

    const window = useConversationStore.getState().byId[CID]?.messages ?? [];
    expect(window.map((m) => m.id)).toEqual(["m0", "m1"]);
    expect(window.find((m) => m.id === "m1")?.content).toBe(
      "thick adopted body that must survive",
    );
  });
});

describe("overlayIncomingWithRicherExisting", () => {
  it("passes through when existing is empty", () => {
    const incoming = [msg("a", "x")];
    expect(overlayIncomingWithRicherExisting(incoming, [])).toBe(incoming);
  });
});
