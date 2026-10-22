// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UserMessage } from "../UserMessage";

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: () => null,
  useConversationFileSourceState: () => ({ source: null, pending: false }),
}));

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  const storeApi = {
    currentConversationId: "c1" as string | null,
    updateMessage: vi.fn(),
  };
  const useConversationStore = Object.assign(
    (sel: (s: typeof storeApi) => unknown) => sel(storeApi),
    { getState: () => storeApi },
  );
  return {
    ...actual,
    useActiveGenerating: () => false,
    useConversationStore,
  };
});

afterEach(() => {
  cleanup();
  useQueuedTurnsStore.setState({ byConversation: {} });
});

function userMsg(over: Partial<Message> = {}): Message {
  return {
    id: "u1",
    role: "user",
    content: "下一句",
    createdAt: "2026-01-01T00:00:00Z",
    executionId: null,
    isStreaming: false,
    ...over,
  };
}

function renderUser(message: Message) {
  return render(
    <TooltipProvider>
      <UserMessage message={message} />
    </TooltipProvider>,
  );
}

describe("UserMessage queued chrome", () => {
  it("排队动作不画在气泡上", () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q1",
      conversationId: "c1",
      messageId: "u1",
      content: "下一句",
      position: 1,
      queueDepth: 1,
    });
    renderUser(userMsg());
    expect(screen.queryByTestId("user-message-queued")).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "软插队" })).toBeNull();
  });
});
