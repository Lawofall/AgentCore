// @vitest-environment jsdom
/**
 * Team-strip fail: 排查包挂气泡「更多」，不挂错误卡 / 状态条。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const execById = vi.hoisted(() => ({
  value: {} as Record<string, { deliveryStatus: null; plan?: unknown }>,
}));

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
      sel: (s: {
        byId: Record<string, { deliveryStatus: null; plan?: unknown }>;
      }) => unknown,
    ) => sel({ byId: execById.value }),
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

vi.mock("@/services/turns/continuePaused", () => ({
  continuePausedTurn: vi.fn(),
}));

vi.mock("../AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => <div data-testid="assistant-footer" />,
  AssistantMessageMetaSummary: () => null,
  MessageMoreMenu: () => (
    <button type="button" aria-label="更多">
      更多
    </button>
  ),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

import { AssistantMessage } from "../AssistantMessage";

function renderBubble(message: Message) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AssistantMessage message={message} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  execById.value = {};
});

describe("AssistantMessage team-strip support pack host", () => {
  it("具名恢复关掉 footer 时仍露出更多，不画红卡上的复制排查包", () => {
    execById.value = {
      "asst-1": { deliveryStatus: null, plan: { agents: [] } },
    };
    renderBubble({
      id: "asst-1",
      role: "assistant",
      content: "",
      createdAt: "2026-08-05T00:00:00Z",
      executionId: null,
      isStreaming: false,
      finishReason: "error",
      error: {
        code: "LLM_TIMEOUT",
        message: "连接超时，请检查网络后重试。",
      },
    });
    expect(screen.queryByText("连接超时，请检查网络后重试。")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
    expect(screen.getByRole("button", { name: "更多" })).toBeTruthy();
  });
});
