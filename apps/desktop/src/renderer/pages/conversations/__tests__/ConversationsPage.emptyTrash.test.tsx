// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationsPage } from "../ConversationsPage";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  trashCount: 3,
}));

vi.mock("../useConversationList", () => ({
  useConversationRouting: () => ({
    selected: "__trash__",
    setSelected: vi.fn(),
    flashId: null,
    folderIds: new Set<string>(),
    folders: [],
    foldersAll: [],
  }),
  useConversationList: () => ({
    conversations: [],
    archived: [],
    counts: {
      ungrouped: 0,
      perFolder: new Map<string, number>(),
      canonical: new Map<string, string>(),
    },
    list: [],
    query: "定价",
    setQuery: vi.fn(),
    staleOnly: false,
    setStaleOnly: vi.fn(),
    isArchivedView: false,
    isTrashView: true,
    trashCount: mocks.trashCount,
    trashList: [],
    deletedConversationList: [],
    retentionDays: 30,
    conversationTrashTotal: 2,
    folderTrashTotal: 1,
    conversationTrashListed: 2,
    folderTrashListed: 1,
    groupedSettled: true,
  }),
}));

vi.mock("../useEmptyRecentlyDeleted", () => ({
  useEmptyRecentlyDeleted: () => ({
    mutate: mocks.mutate,
    isPending: false,
  }),
}));

vi.mock("../useConversationBulkSelect", () => ({
  useConversationBulkSelect: () => ({
    selectMode: false,
    setSelectMode: vi.fn(),
    exitSelectMode: vi.fn(),
    selectedIds: new Set<string>(),
    allVisibleSelected: false,
    toggleSelectAll: vi.fn(),
    toggleSelected: vi.fn(),
    handleBulkArchive: vi.fn(),
    handleBulkUnarchive: vi.fn(),
    handleBulkDelete: vi.fn(),
  }),
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

afterEach(() => {
  cleanup();
  mocks.mutate.mockReset();
  mocks.trashCount = 3;
});

describe("ConversationsPage empty trash", () => {
  it("confirms the whole bin, including while a search is active", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(screen.getByText("清空最近删除？")).toBeTruthy();
    expect(screen.getByText(/2 条对话，以及 1 个文件夹/)).toBeTruthy();
    expect(screen.getByText(/电脑上的文件夹不会被删除/)).toBeTruthy();
    expect(screen.getByText(/当前搜索不会缩小清空范围/)).toBeTruthy();

    const confirms = screen.getAllByRole("button", { name: "清空" });
    fireEvent.click(confirms[confirms.length - 1]);
    expect(mocks.mutate).toHaveBeenCalledTimes(1);
  });

  it("hides 清空 when the bin is empty", () => {
    mocks.trashCount = 0;
    renderPage();
    expect(screen.queryByRole("button", { name: "清空" })).toBeNull();
  });
});
