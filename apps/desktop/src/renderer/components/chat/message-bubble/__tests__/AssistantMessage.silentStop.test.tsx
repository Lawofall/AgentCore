// @vitest-environment jsdom
/**
 * ask_user 结算：取消/确认都占存根；正文只有「就是那句问句」时才藏，续聊露出。
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

describe("AssistantMessage ask settled", () => {
  it("stop resolved + 空 content：画「已取消本回合」，不把问句回落成正文", () => {
    cardsMock.checkpoints = [{ ...baseCheckpoint, decision: "stop" }];
    renderBubble(settledMessage());
    expect(screen.queryByTestId("assistant-body")).toBeNull();
    expect(screen.getByText("已取消本回合")).toBeTruthy();
    expect(document.body.textContent).not.toContain(baseCheckpoint.question);
  });

  it("continue resolved + 空 content：藏空正文，保留成功存根", () => {
    cardsMock.checkpoints = [
      { ...baseCheckpoint, decision: "continue", note: "就按这个开做" },
    ];
    renderBubble(settledMessage());
    expect(screen.queryByTestId("assistant-body")).toBeNull();
    expect(screen.queryByText("已按你的决定继续")).toBeNull();
    expect(screen.getByText("就按这个开做")).toBeTruthy();
  });

  it("continue resolved + CEO 续聊：存根与续聊正文同时在", () => {
    cardsMock.checkpoints = [
      { ...baseCheckpoint, decision: "continue", note: "就按这个开做" },
    ];
    renderBubble(
      settledMessage({ content: "好，按你确认的默认项来——先不派活，继续聊。" }),
    );
    expect(document.body.textContent).toContain(
      "好，按你确认的默认项来——先不派活，继续聊。",
    );
    expect(screen.queryByText("已按你的决定继续")).toBeNull();
    expect(screen.getByText("就按这个开做")).toBeTruthy();
  });

  it("正文就是问句副本时仍藏（避免贴在结论文旁像还在催）", () => {
    cardsMock.checkpoints = [
      { ...baseCheckpoint, decision: "continue", note: "" },
    ];
    renderBubble(settledMessage({ content: baseCheckpoint.question }));
    expect(screen.queryByTestId("assistant-body")).toBeNull();
    expect(screen.queryByText("已按你的决定继续")).toBeNull();
    expect(document.querySelector("[data-ask-status='resolved']")).toBeTruthy();
  });
});
