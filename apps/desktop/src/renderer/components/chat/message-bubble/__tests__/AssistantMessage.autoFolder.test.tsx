// @vitest-environment jsdom
/**
 * 裸聊自动建文件夹的落点告知，挂在气泡的哪里。
 *
 * 建桌发生在派工前、文件还没写——挂气泡顶部就成了「文件已存到新建的文件夹」抢在 AI 说
 * 「好的，这就安排」之前，时序是倒的。所以告知一律排在正文之后：有产出文件时并进「本回合
 * 产出文件」卡头部（落点和落进去的文件一处说清），没有产出文件时才独立成卡——那条边界真实
 * 存在（写盘失败但文件夹已建、且会被后续回合复用），告知不能跟着丢。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys, workspaceKeys } from "@/lib/queryKeys";
import type { FolderMeta } from "@/services/folders";
import type { Message } from "@/stores/conversation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { type ReactNode, useState } from "react";
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

vi.mock("@/stores/execution", () => ({
  useExecutionStore: (
    sel: (s: {
      byId: Record<string, { deliveryStatus: { artifacts: unknown[] } }>;
    }) => unknown,
  ) => sel({ byId: { "asst-1": { deliveryStatus: state } } }),
}));

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: [],
      planReviews: [],
      teamPreviews: [],
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

vi.mock("@/components/chat/debate/CollapsibleSpeech", () => ({
  CollapsibleSpeech: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

// 产出卡的旁路依赖（右坞 / 预览能力）不是本文件的议题。
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

const AUTO_FOLDER: FolderMeta = {
  id: "f-auto",
  name: "季度复盘",
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
  relPath: "季度复盘",
  parentRelPath: null,
};

function settledMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "好的，这就安排。",
    createdAt: "2026-08-13T00:00:00Z",
    executionId: null,
    isStreaming: false,
    finishReason: "end_turn",
    autoFolder: { folderId: "f-auto", name: "季度复盘" },
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
  client.setQueryData(conversationKeys.grouped, {
    folders: [AUTO_FOLDER],
    conversations: [],
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

/** 告知是否排在答复正文之后（顶部回潮的绊线）。 */
function noticeFollowsBody(): boolean {
  const body = screen.getByTestId("assistant-body");
  const notice = screen.getByTestId("auto-folder-notice");
  return !!(
    body.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING
  );
}

beforeEach(() => {
  state.artifacts = [];
});
afterEach(cleanup);

describe("AssistantMessage · 裸聊落点告知的落点", () => {
  it("主路径（有产出文件）：告知在产出卡头部，正文之后，不再单独占一张卡", () => {
    state.artifacts = [{ path: "notes.md", status: "accepted" }];
    renderBubble(settledMessage());

    expect(screen.getByText("本回合产出文件")).toBeTruthy();
    expect(screen.getByTestId("auto-folder-notice")).toBeTruthy();
    expect(screen.queryByTestId("auto-folder-notice-card")).toBeNull();
    expect(noticeFollowsBody()).toBe(true);
  });

  it("边界（建了桌但没产出文件）：告知独立成卡，仍在正文之后", () => {
    renderBubble(settledMessage());

    expect(screen.queryByText("本回合产出文件")).toBeNull();
    expect(screen.getByTestId("auto-folder-notice-card")).toBeTruthy();
    expect(noticeFollowsBody()).toBe(true);
  });

  it("没建桌的普通回合：两种形态都不出现", () => {
    state.artifacts = [{ path: "notes.md", status: "accepted" }];
    renderBubble(settledMessage({ autoFolder: undefined }));

    expect(screen.getByText("本回合产出文件")).toBeTruthy();
    expect(screen.queryByTestId("auto-folder-notice")).toBeNull();
    expect(screen.queryByTestId("auto-folder-notice-card")).toBeNull();
  });
});
