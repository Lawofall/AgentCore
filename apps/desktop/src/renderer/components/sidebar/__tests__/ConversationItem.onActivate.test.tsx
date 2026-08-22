// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Conversation } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  switchConversation: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useArchiveConversation: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDeleteConversation: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDuplicateConversation: () => ({ mutate: vi.fn() }),
  useRenameConversation: () => ({ mutate: vi.fn() }),
  useRestoreConversation: () => ({ mutate: vi.fn() }),
  useTogglePin: () => ({ mutate: vi.fn() }),
  useUnarchiveConversation: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: {
      currentConversationId: string;
      byId: Record<string, never>;
      switchConversation: () => void;
      dropConversationRuntime: () => void;
    }) => unknown,
  ) =>
    sel({
      currentConversationId: "c1",
      byId: {},
      switchConversation: mocks.switchConversation,
      dropConversationRuntime: vi.fn(),
    }),
  useConversationGenerating: () => false,
}));

vi.mock("@/stores/aiAttention", () => ({
  useConversationAwaitingAttention: () => false,
}));

vi.mock("@/stores/aiTurnActivity", () => ({
  conversationSidebarActivityStatus: () => null,
  useConversationCloudRunning: () => false,
}));

vi.mock("@/stores/interactions", () => ({
  isAwaitingUserEntry: () => false,
  useInteractionStore: (sel: (s: { byId: Map<string, never> }) => unknown) =>
    sel({ byId: new Map<string, never>() }),
}));

vi.mock("@/stores/pausedTurns", () => ({
  usePausedTurnStore: (sel: (s: { pending: never[] }) => unknown) =>
    sel({ pending: [] }),
}));

vi.mock("@/stores/share", () => ({
  useShareStore: { getState: () => ({ open: vi.fn() }) },
}));

import { ConversationItem } from "@/components/sidebar/ConversationItem";

const conv: Conversation = {
  id: "c1",
  title: "当前会话",
  updatedAt: "2026-08-01T00:00:00Z",
  messageCount: 1,
  lastMessagePreview: null,
};

afterEach(() => {
  cleanup();
  mocks.switchConversation.mockReset();
});

describe("ConversationItem onActivate", () => {
  it("fires onActivate even when this chat is already current", () => {
    const onActivate = vi.fn();

    render(
      <MemoryRouter initialEntries={["/conversations/c1"]}>
        <TooltipProvider>
          <ConversationItem conversation={conv} onActivate={onActivate} />
        </TooltipProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText("当前会话"));

    expect(mocks.switchConversation).toHaveBeenCalledWith("c1");
    expect(onActivate).toHaveBeenCalledTimes(1);
  });
});
