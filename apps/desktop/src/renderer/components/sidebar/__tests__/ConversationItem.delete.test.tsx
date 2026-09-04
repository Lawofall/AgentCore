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
  mutateAsync: vi.fn().mockResolvedValue(undefined),
  dropConversationRuntime: vi.fn(),
  restoreMutate: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useArchiveConversation: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDeleteConversation: () => ({
    mutate: vi.fn(),
    mutateAsync: mocks.mutateAsync,
  }),
  useDuplicateConversation: () => ({ mutate: vi.fn() }),
  useRenameConversation: () => ({ mutate: vi.fn() }),
  useRestoreConversation: () => ({ mutate: mocks.restoreMutate }),
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
      switchConversation: vi.fn(),
      dropConversationRuntime: mocks.dropConversationRuntime,
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

import { ConversationItem } from "@/components/sidebar/ConversationItem";

const conv: Conversation = {
  id: "c1",
  title: "当前会话",
  updatedAt: "2026-08-01T00:00:00Z",
  messageCount: 1,
  lastMessagePreview: null,
};

function renderItem() {
  return render(
    <MemoryRouter initialEntries={["/conversations/c1"]}>
      <TooltipProvider>
        <ConversationItem conversation={conv} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

function expectNoDeleteConfirm() {
  expect(screen.queryByText(/确认删除/)).toBeNull();
  expect(screen.queryByLabelText("取消")).toBeNull();
}

async function expectSoftDeleted() {
  expect(mocks.mutateAsync).toHaveBeenCalledTimes(1);
  expect(mocks.mutateAsync).toHaveBeenCalledWith("c1");
  expectNoDeleteConfirm();
  await waitFor(() =>
    expect(notifyInfo).toHaveBeenCalledWith(
      "已删除对话",
      expect.objectContaining({
        description: "当前会话",
        action: expect.objectContaining({ label: "撤销" }),
      }),
    ),
  );
  expect(mocks.dropConversationRuntime).toHaveBeenCalledWith("c1");
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
  mocks.mutateAsync.mockReset();
  mocks.mutateAsync.mockResolvedValue(undefined);
  mocks.dropConversationRuntime.mockReset();
  mocks.restoreMutate.mockReset();
  vi.mocked(notifyInfo).mockReset();
});

afterEach(() => {
  cleanup();
});

describe("ConversationItem delete", () => {
  it("soft-deletes from the context menu with no confirm step", async () => {
    renderItem();

    fireEvent.contextMenu(screen.getByText("当前会话"));
    fireEvent.click(await screen.findByText("删除对话"));

    expectNoDeleteConfirm();
    await expectSoftDeleted();
  });

  it("soft-deletes from the more menu with no confirm step", async () => {
    renderItem();

    const surface = screen.getByRole("button", {
      name: /当前会话/,
    }).parentElement;
    if (!surface) throw new Error("conversation row surface missing");
    fireEvent.mouseEnter(surface);
    const more = screen.getByLabelText("更多操作");
    fireEvent.pointerDown(more);
    fireEvent.click(more);
    fireEvent.click(await screen.findByText("删除对话"));

    expectNoDeleteConfirm();
    await expectSoftDeleted();
  });

  it("undoes via the delete toast without a confirm click", async () => {
    renderItem();

    fireEvent.contextMenu(screen.getByText("当前会话"));
    fireEvent.click(await screen.findByText("删除对话"));
    await expectSoftDeleted();

    const opts = vi.mocked(notifyInfo).mock.calls[0]?.[1] as
      | { action?: { onClick: () => void } }
      | undefined;
    opts?.action?.onClick();
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c1");
  });
});

describe("ConversationItem hover actions", () => {
  it("keeps rename in 更多, not as a hover icon", async () => {
    renderItem();

    const surface = screen.getByRole("button", {
      name: /当前会话/,
    }).parentElement;
    if (!surface) throw new Error("conversation row surface missing");
    fireEvent.mouseEnter(surface);

    expect(screen.queryByLabelText("重命名")).toBeNull();
    expect(screen.getByLabelText("归档")).toBeTruthy();

    const more = screen.getByLabelText("更多操作");
    fireEvent.pointerDown(more);
    fireEvent.click(more);
    expect(
      await screen.findByRole("menuitem", { name: "重命名" }),
    ).toBeTruthy();
  });
});
