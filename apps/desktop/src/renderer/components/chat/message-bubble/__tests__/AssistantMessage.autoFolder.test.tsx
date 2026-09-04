// @vitest-environment jsdom
/**
 * 裸聊自动建文件夹：对话内不再画落点告知（产出卡头一行 / 无文件时的独立卡）。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { workspaceKeys } from "@/lib/queryKeys";
import type { Message } from "@/stores/conversation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  artifacts: [] as { path: string; status: string }[],
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
        byId: Record<string, { deliveryStatus: { artifacts: unknown[] } }>;
      }) => unknown,
    ) => sel({ byId: { "asst-1": { deliveryStatus: state } } }),
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

vi.mock("@/services/turns", () => ({ runRegenerate: vi.fn() }));

vi.mock("../AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => <div data-testid="assistant-footer" />,
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => (
    <div data-testid="assistant-body">{content}</div>
  ),
}));

vi.mock("@/stores/disclosure", () => ({
  usePersistentDisclosure: (_key: string | null, initial: boolean) =>
    useState(initial),
}));
vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (
    sel: (s: { showFile: () => void; showChanges: () => void }) => unknown,
  ) => sel({ showFile: vi.fn(), showChanges: vi.fn() }),
}));
vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: () => null,
}));
vi.mock("@/hooks/useWorkspaces", () => ({
  useConversationWorkspace: () => null,
}));

import { AssistantMessage } from "../AssistantMessage";

function settledMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "好的，这就安排。",
    createdAt: "2026-08-13T00:00:00Z",
    executionId: null,
    isStreaming: false,
    finishReason: "end_turn",
    ...overrides,
  };
}

function renderBubble(message: Message) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
  client.setQueryData(workspaceKeys.list, []);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <AssistantMessage message={message} />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function expectNoLandingNotice() {
  expect(screen.queryByText("文件已存到新建的文件夹")).toBeNull();
  expect(screen.queryByText("已为这次对话新建文件夹")).toBeNull();
  expect(screen.queryByTestId("auto-folder-notice")).toBeNull();
  expect(screen.queryByTestId("auto-folder-notice-card")).toBeNull();
}

beforeEach(() => {
  state.artifacts = [];
});
afterEach(cleanup);

describe("AssistantMessage · 对话内不渲染裸聊落点告知", () => {
  it("有产出文件：不出产出卡，也没有落点条", () => {
    state.artifacts = [{ path: "notes.md", status: "accepted" }];
    renderBubble(settledMessage());

    expect(screen.queryByText("本回合产出文件")).toBeNull();
    expect(screen.getByTestId("assistant-body")).toBeTruthy();
    expectNoLandingNotice();
  });

  it("没有产出文件：不出独立落点卡", () => {
    renderBubble(settledMessage());

    expect(screen.queryByText("本回合产出文件")).toBeNull();
    expectNoLandingNotice();
  });
});
