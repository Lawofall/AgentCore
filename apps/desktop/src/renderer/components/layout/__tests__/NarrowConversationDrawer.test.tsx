// @vitest-environment jsdom
import type { DeletedConversationMeta } from "@/services/conversations";
import type { FolderMeta } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const conversations: Conversation[] = [];
const folders: FolderMeta[] = [];
const trashItems: DeletedConversationMeta[] = [];
const requiredIds = new Set<string>();
const restore = vi.fn();
const setConversationDrawerOpen = vi.fn();

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow: true,
    hideChrome: false,
    conversationDrawerOpen: true,
    setConversationDrawerOpen,
  }),
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => conversations,
  useConversationTrash: () => ({
    data: { items: trashItems, retentionDays: 30 },
    isLoading: false,
  }),
  useRestoreConversation: () => ({ mutate: restore, isPending: false }),
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => folders,
}));

vi.mock("@/stores/aiAttention", () => ({
  useRequiredConversationIds: () => requiredIds,
}));

vi.mock("@/components/sidebar/ConversationItem", () => ({
  ConversationItem: ({
    conversation,
    onActivate,
  }: {
    conversation: Conversation;
    onActivate?: () => void;
  }) => (
    <button
      type="button"
      data-testid={`conv-${conversation.id}`}
      onClick={() => onActivate?.()}
    >
      {conversation.title}
    </button>
  ),
}));

vi.mock("@/components/sidebar/WorkspaceGroupHeader", () => ({
  WorkspaceGroupHeader: ({
    folder,
    expanded,
    onToggleExpanded,
  }: {
    folder: { id: string; name: string };
    expanded: boolean;
    onToggleExpanded: () => void;
  }) => (
    <div>
      <span data-testid={`group-${folder.id}-expanded`}>
        {expanded ? "open" : "closed"}
      </span>
      <button type="button" onClick={onToggleExpanded}>
        折叠 {folder.name}
      </button>
    </div>
  ),
}));

import { NarrowConversationDrawer } from "@/components/layout/NarrowConversationDrawer";

function makeConv(
  id: string,
  opts: { folderId?: string | null; pinned?: boolean; title?: string } = {},
): Conversation {
  return {
    id,
    title: opts.title ?? id,
    updatedAt: "2026-08-01T00:00:00Z",
    messageCount: 1,
    lastMessagePreview: null,
    folderId: opts.folderId ?? null,
    pinned: opts.pinned,
  };
}

function renderDrawer() {
  return render(<NarrowConversationDrawer />);
}

beforeEach(() => {
  conversations.length = 0;
  folders.length = 0;
  trashItems.length = 0;
  requiredIds.clear();
  restore.mockReset();
  setConversationDrawerOpen.mockReset();
});

afterEach(cleanup);

describe("NarrowConversationDrawer", () => {
  it("closes when the current conversation row is activated", () => {
    conversations.push(makeConv("c1", { title: "当前会话" }));

    renderDrawer();
    fireEvent.click(screen.getByTestId("conv-c1"));

    expect(setConversationDrawerOpen).toHaveBeenCalledWith(false);
  });

  it("enters 最近删除 and restores a conversation", () => {
    conversations.push(makeConv("live"));
    trashItems.push({
      id: "gone",
      title: "定价讨论",
      folderId: null,
      messageCount: 4,
      deletedAt: "2026-08-20T00:00:00Z",
      purgeAt: "2026-09-19T00:00:00Z",
    });

    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "最近删除" }));

    expect(screen.getByText("最近删除")).toBeTruthy();
    expect(screen.getByText("定价讨论")).toBeTruthy();
    expect(screen.queryByText("彻底删除")).toBeNull();
    expect(screen.queryByText(/文件夹/)).toBeNull();

    fireEvent.click(screen.getByLabelText("恢复对话 定价讨论"));

    expect(restore).toHaveBeenCalledWith("gone");
  });

  it("hides local folders when this runtime has no local disk", () => {
    folders.push({
      id: "local-1",
      name: "本机项目",
      mode: "local",
      localRootId: "root-1",
      localSubpath: null,
    });
    conversations.push(
      makeConv("local-c", { folderId: "local-1", title: "本机会话" }),
    );

    renderDrawer();
    expect(screen.queryByTestId("group-local-1-expanded")).toBeNull();
    expect(screen.queryByTestId("conv-local-c")).toBeNull();
  });

  it("keeps a folded group open when a member is 等你", () => {
    folders.push({
      id: "f1",
      name: "商标案",
      mode: "cloud",
      localRootId: null,
      localSubpath: null,
    });
    conversations.push(
      makeConv("need-you", { folderId: "f1", title: "待拍板" }),
    );

    renderDrawer();
    expect(screen.getByTestId("group-f1-expanded").textContent).toBe("open");

    fireEvent.click(screen.getByRole("button", { name: "折叠 商标案" }));
    expect(screen.getByTestId("group-f1-expanded").textContent).toBe("closed");
    expect(screen.queryByTestId("conv-need-you")).toBeNull();

    cleanup();
    requiredIds.add("need-you");
    renderDrawer();

    expect(screen.getByTestId("group-f1-expanded").textContent).toBe("open");
    expect(screen.getByTestId("conv-need-you")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "折叠 商标案" }));
    expect(screen.getByTestId("group-f1-expanded").textContent).toBe("open");
    expect(screen.getByTestId("conv-need-you")).toBeTruthy();
  });
});
