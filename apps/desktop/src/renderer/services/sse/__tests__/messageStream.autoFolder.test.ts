import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CID = "conv-auto-folder";

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.setTurnPhase("streaming", CID);
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "写一份纪要",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  conv.createAssistantMessage(CID);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("auto_folder_created SSE", () => {
  it("消费事件并刷新文件夹列表，不往气泡上挂落点告知", () => {
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    const ok = handleMessageStreamEvent(
      {
        type: "auto_folder_created",
        timestamp: "",
        payload: { folder_id: "f-auto", name: "季度复盘" },
      },
      { conversationId: CID, source: "server" },
    );

    expect(ok).toBe(true);
    expect(spy).toHaveBeenCalledWith({
      queryKey: conversationKeys.grouped,
    });
    const assistants = getRuntime(CID).messages.filter(
      (m) => m.role === "assistant",
    );
    expect(assistants).toHaveLength(1);
    expect(assistants[0]).not.toHaveProperty("autoFolder");
  });
});
