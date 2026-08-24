// @vitest-environment jsdom
/**
 * 「曾中断恢复」标记（本次审计议题 D5）。
 *
 * 崩溃重驱把成果写回原回合，用户看到的是「这条消息自己跑完了」——正因为如此，
 * 气泡必须明说它曾经断过，不许静默假装一次跑完。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    useActiveGenerating: () => false,
    useConversationStore: (
      sel: (s: { currentConversationId: string | null }) => unknown,
    ) => sel({ currentConversationId: "conv-1" }),
    getActiveRuntime: () => ({ messages: [] }),
    assistantProjectionId: (m: { id: string }) => m.id,
  };
});

vi.mock("@/stores/usage", () => ({
  useUsageStore: (
    sel: (s: {
      loadMessageCost: () => void;
      messageCosts: Record<string, never>;
    }) => unknown,
  ) => sel({ loadMessageCost: () => {}, messageCosts: {} }),
}));

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useExecutionStore: (
      sel: (s: { byId: Record<string, { deliveryStatus: null }> }) => unknown,
    ) => sel({ byId: {} }),
    useMessageExecution: () => null,
  };
});

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: [],
      planReviews: [],
    }),
  };
});

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("../AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => <div data-testid="assistant-footer" />,
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => (
    <div data-testid="assistant-body">{content}</div>
  ),
}));

vi.mock("@/components/chat/debate/CollapsibleSpeech", () => ({
  CollapsibleSpeech: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import { AssistantMessage } from "../AssistantMessage";

function settledMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "崩溃前后拼起来的完整成果",
    createdAt: "2026-08-13T00:00:00Z",
    executionId: null,
    isStreaming: false,
    finishReason: "end_turn",
    ...overrides,
  };
}

function renderBubble(message: Message) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AssistantMessage message={message} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("AssistantMessage recovered marker", () => {
  it("恢复回合：成果照常显示，同时挂「曾中断恢复」", () => {
    renderBubble(settledMessage({ recovered: true }));
    expect(screen.getByText("曾中断恢复")).toBeTruthy();
    expect(screen.getByTestId("assistant-body").textContent).toBe(
      "崩溃前后拼起来的完整成果",
    );
  });

  it("正常回合不挂标记（不给每条消息加噪音）", () => {
    renderBubble(settledMessage());
    expect(screen.queryByText("曾中断恢复")).toBeNull();
  });
});
