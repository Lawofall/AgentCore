// @vitest-environment jsdom
/**
 * ask_user 取消结算：不画「已取消本回合」存根；content 空时用 checkpoint.question
 * 作展示正文。continue 等仍藏正文、只留 stub。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { CheckpointDisplay, Message } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const cardsMock = vi.hoisted(() => ({
  checkpoints: [] as CheckpointDisplay[],
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
      sel: (s: { byId: Record<string, { deliveryStatus: null }> }) => unknown,
    ) => sel({ byId: {} }),
  };
});

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: cardsMock.checkpoints,
      planReviews: [],
      teamPreviews: [],
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

const baseCheckpoint: CheckpointDisplay = {
  id: "cp-1",
  question: "关于论文有几个方向想先跟你对齐",
  assumptions: [],
  questions: [],
  intent: "decision",
  status: "resolved",
  decision: "stop",
  note: "",
  selected: [],
};

function settledMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "",
    createdAt: "2026-08-05T00:00:00Z",
    executionId: null,
    isStreaming: false,
    // 有交互卡必有时间线标记——否则会踩空取消气泡 omit。
    process: [{ kind: "checkpoint", checkpoint_id: "cp-1" }],
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

afterEach(() => {
  cleanup();
  cardsMock.checkpoints = [];
});

describe("AssistantMessage silent ask stop", () => {
  it("stop resolved + 空 content：问句作普通正文，无「已取消本回合」", () => {
    cardsMock.checkpoints = [{ ...baseCheckpoint, decision: "stop" }];
    renderBubble(settledMessage());
    expect(screen.getByTestId("assistant-body").textContent).toBe(
      baseCheckpoint.question,
    );
    expect(screen.queryByText("已取消本回合")).toBeNull();
  });

  it("continue resolved：仍藏正文（不回落问句），保留存根", () => {
    cardsMock.checkpoints = [
      { ...baseCheckpoint, decision: "continue", note: "就按这个开做" },
    ];
    renderBubble(settledMessage());
    expect(screen.queryByTestId("assistant-body")).toBeNull();
    expect(screen.getByText("已按你的决定继续")).toBeTruthy();
  });
});
