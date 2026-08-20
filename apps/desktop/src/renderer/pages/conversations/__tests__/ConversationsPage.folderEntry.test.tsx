// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationsPage } from "../ConversationsPage";

const { folder, conversation } = vi.hoisted(() => {
  const folder = {
    id: "f1",
    name: "产品设计",
    mode: "cloud" as const,
    localRootId: null,
    localSubpath: null,
  };
  const conversation = {
    id: "c1",
    title: "路线图讨论",
    updatedAt: new Date().toISOString(),
    messageCount: 4,
    lastMessagePreview: "下周一补竞品对标。",
    folderId: "f1",
  };
  return { folder, conversation };
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
    conversations: [conversation],
    archived: [],
    counts: { ungrouped: 0, perFolder: new Map([["f1", 1]]) },
    list: [conversation],
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
});

describe("ConversationsPage folder entry", () => {
  it("does not keep a persistent 管理文件夹 jump on the conversation list", () => {
    renderPage();
    expect(screen.queryByText("管理文件夹")).toBeNull();
    expect(screen.getByText("产品设计")).toBeTruthy();
    expect(screen.getByText("路线图讨论")).toBeTruthy();
  });
});
