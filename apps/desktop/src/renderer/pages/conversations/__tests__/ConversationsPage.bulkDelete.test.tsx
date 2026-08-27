// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationsPage } from "../ConversationsPage";

const mocks = vi.hoisted(() => ({
  deleteAsync: vi.fn().mockResolvedValue(undefined),
  restoreMutate: vi.fn(),
  notifyConversationDeleted: vi.fn(),
}));

const { folder, conversations } = vi.hoisted(() => {
  const folder = {
    id: "f1",
    name: "产品设计",
    mode: "cloud" as const,
    localRootId: null,
    localSubpath: null,
  };
  const conversations = [
    {
      id: "c1",
      title: "路线图讨论",
      updatedAt: new Date().toISOString(),
      messageCount: 4,
      lastMessagePreview: "下周一补竞品对标。",
      folderId: "f1",
    },
    {
      id: "c2",
      title: "定价草案",
      updatedAt: new Date().toISOString(),
      messageCount: 2,
      lastMessagePreview: "先出一版报价。",
      folderId: "f1",
    },
  ];
  return { folder, conversations };
});

vi.mock("../useConversationList", () => ({
  useConversationRouting: () => ({
    selected: "__all__",
    setSelected: vi.fn(),
    flashId: null,
    folderIds: new Set(["f1"]),
    folders: [folder],
  }),
  useConversationList: () => ({
    conversations,
    archived: [],
    counts: { ungrouped: 0, perFolder: new Map([["f1", 2]]) },
    list: conversations,
    query: "",
    setQuery: vi.fn(),
    staleOnly: false,
    setStaleOnly: vi.fn(),
    isArchivedView: false,
    isTrashView: false,
    trashCount: 0,
    trashList: [],
    deletedConversationList: [],
    retentionDays: 30,
  }),
}));

vi.mock("../ConversationManageRow", () => ({
  ConversationManageRow: ({
    conversation: row,
  }: {
    conversation: { title: string };
  }) => <div>{row.title}</div>,
}));

vi.mock("../CollaborationTimeline", () => ({
  CollaborationTimelinePanel: () => null,
}));

vi.mock("@/hooks/useConversations", () => ({
  useArchiveConversation: () => ({ mutateAsync: vi.fn() }),
  useDeleteConversation: () => ({ mutateAsync: mocks.deleteAsync }),
  useRestoreConversation: () => ({ mutate: mocks.restoreMutate }),
  useUnarchiveConversation: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/lib/conversationDeleteCopy", () => ({
  notifyConversationDeleted: (...args: unknown[]) =>
    mocks.notifyConversationDeleted(...args),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <ConversationsPage />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function expectNoConfirmDelete() {
  expect(screen.queryByRole("button", { name: "确认删除" })).toBeNull();
  expect(screen.queryByText(/删除 \d+ 项？/)).toBeNull();
}

beforeEach(() => {
  mocks.deleteAsync.mockReset();
  mocks.deleteAsync.mockResolvedValue(undefined);
  mocks.restoreMutate.mockReset();
  mocks.notifyConversationDeleted.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("ConversationsPage bulk delete", () => {
  it("deletes on 删除 with no confirm step, and undo restores those ids", async () => {
    renderPage();

    fireEvent.click(screen.getByLabelText("批量选择"));
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    expect(screen.getByText("已选 2 项")).toBeTruthy();
    expect(screen.getByRole("button", { name: "批量归档" })).toBeTruthy();
    expectNoConfirmDelete();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expectNoConfirmDelete();

    await waitFor(() => {
      expect(mocks.deleteAsync).toHaveBeenCalledTimes(2);
    });
    expect(mocks.deleteAsync).toHaveBeenCalledWith("c1");
    expect(mocks.deleteAsync).toHaveBeenCalledWith("c2");
    expect(mocks.notifyConversationDeleted).toHaveBeenCalledWith(
      "2 条",
      expect.any(Function),
    );

    const onUndo = mocks.notifyConversationDeleted.mock.calls[0]?.[1] as
      | (() => void)
      | undefined;
    onUndo?.();
    expect(mocks.restoreMutate).toHaveBeenCalledTimes(2);
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c1");
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c2");
  });

  it("undo after a mid-batch failure restores only ids that already succeeded", async () => {
    mocks.deleteAsync.mockImplementation(async (id: string) => {
      if (id === "c2") throw new Error("busy");
    });

    renderPage();

    fireEvent.click(screen.getByLabelText("批量选择"));
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expectNoConfirmDelete();

    await waitFor(() => {
      expect(mocks.notifyConversationDeleted).toHaveBeenCalledWith(
        "1 条",
        expect.any(Function),
      );
    });

    const onUndo = mocks.notifyConversationDeleted.mock.calls[0]?.[1] as
      | (() => void)
      | undefined;
    onUndo?.();
    expect(mocks.restoreMutate).toHaveBeenCalledTimes(1);
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c1");
    expect(mocks.restoreMutate).not.toHaveBeenCalledWith("c2");
  });
});
