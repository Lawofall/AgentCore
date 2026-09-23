import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { beforeEach, describe, expect, it } from "vitest";

const CID = "conv-window-prompt";

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.setTurnPhase("streaming", CID);
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "继续",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  conv.createAssistantMessage(CID);
});

function prompt(n: number) {
  return handleMessageStreamEvent(
    {
      type: "window_prompt",
      timestamp: "",
      payload: { last_prompt_tokens: n },
    },
    { conversationId: CID, source: "server" },
  );
}

describe("window_prompt SSE", () => {
  it("writes the session waterline and leaves the bubble receipt empty", () => {
    expect(prompt(120_000)).toBe(true);
    const rt = getRuntime(CID);
    const assistant = rt.messages.find((m) => m.role === "assistant");
    expect(rt.ceoWindowTokens).toBe(120_000);
    expect(assistant?.usage).toBeUndefined();
  });

  it("a later measurement replaces the waterline after the bubble already has a receipt", () => {
    prompt(90_000);
    useConversationStore.getState().attachTurnMetaToLastMessage(
      {
        usage: {
          input: 90_000,
          output: 10,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
          last_prompt: 90_000,
        },
      },
      CID,
    );
    prompt(40_000);
    const rt = getRuntime(CID);
    const assistant = rt.messages.find((m) => m.role === "assistant");
    expect(rt.ceoWindowTokens).toBe(40_000);
    expect(assistant?.usage?.last_prompt).toBe(90_000);
  });
});
