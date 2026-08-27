// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { notifyInfo } from "@/lib/toast";
import type { Conversation } from "@/stores/conversation";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const mocks = vi.hoisted(() => ({
  deleteAsync: vi.fn().mockResolvedValue(undefined),
  restore: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useArchiveConversation: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDeleteConversation: () => ({ mutateAsync: mocks.deleteAsync }),
  useDuplicateConversation: () => ({ mutate: vi.fn() }),
  useRenameConversation: () => ({ mutate: vi.fn() }),
  useRestoreConversation: () => ({ mutate: mocks.restore }),
  useTogglePin: () => ({ mutate: vi.fn() }),
  useUnarchiveConversation: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: {
      currentConversationId: string | null;
      switchConversation: () => void;
      dropConversationRuntime: () => void;
    }) => unknown,
  ) =>
    sel({
      currentConversationId: null,
      switchConversation: vi.fn(),
      dropConversationRuntime: vi.fn(),
    }),
  useConversationGenerating: () => false,
}));

vi.mock("@/stores/aiAttention", () => ({
  useConversationAwaitingAttention: () => false,
}));

vi.mock("@/stores/interactions", () => ({
  isAwaitingUserEntry: () => false,
  isColdResumeKind: () => false,
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

import { ConversationManageRow } from "../ConversationManageRow";

const conv: Conversation = {
  id: "c1",
  title: "定价讨论",
  updatedAt: "2026-08-01T00:00:00Z",
  messageCount: 3,
  lastMessagePreview: "先看回收站。",
};

function renderRow() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ConversationManageRow conversation={conv} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

function rowGroup() {
  return screen.getByText("定价讨论").closest(".group") as HTMLElement;
}

function expectNoConfirmStep() {
  expect(screen.queryByText(/确认删除/)).toBeNull();
  expect(screen.queryByLabelText("取消")).toBeNull();
}

function clickUndo() {
  const opts = vi.mocked(notifyInfo).mock.calls[0]?.[1] as
    | { action?: { onClick?: () => void } }
    | undefined;
  opts?.action?.onClick?.();
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView ??= () => {};
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
});

beforeEach(() => {
  mocks.deleteAsync.mockReset();
  mocks.deleteAsync.mockResolvedValue(undefined);
  mocks.restore.mockReset();
  vi.mocked(notifyInfo).mockReset();
});

afterEach(cleanup);

describe("ConversationManageRow delete", () => {
  it("soft-deletes from the more menu without a confirm step, and undo restores", async () => {
    renderRow();
    fireEvent.mouseEnter(rowGroup());
    const more = screen.getByLabelText("更多操作");
    fireEvent.pointerDown(more);
    fireEvent.click(more);
    fireEvent.click(await screen.findByText("删除对话"));

    await waitFor(() => expect(mocks.deleteAsync).toHaveBeenCalledWith("c1"));
    expectNoConfirmStep();
    expect(notifyInfo).toHaveBeenCalledWith(
      "已删除对话",
      expect.objectContaining({
        description: "定价讨论",
        action: expect.objectContaining({ label: "撤销" }),
      }),
    );

    clickUndo();
    expect(mocks.restore).toHaveBeenCalledWith("c1");
  });

  it("soft-deletes from the context menu without a confirm step", async () => {
    renderRow();
    fireEvent.contextMenu(rowGroup());
    fireEvent.click(await screen.findByText("删除对话"));

    await waitFor(() => expect(mocks.deleteAsync).toHaveBeenCalledWith("c1"));
    expectNoConfirmStep();
  });
});
