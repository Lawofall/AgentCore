// @vitest-environment jsdom
import type { Conversation } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PinnedConversations } from "../PinnedConversations";
import { RecentConversations } from "../RecentConversations";
import { WorkspaceGroups } from "../WorkspaceGroups";

const convs: Conversation[] = [];
let groups: {
  folder: {
    id: string;
    name: string;
    mode: "cloud";
    localRootId: null;
    localSubpath: null;
  };
  convs: Conversation[];
  latest: number;
}[] = [];

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => convs,
}));

vi.mock("@/hooks/useWorkspaceGroups", () => ({
  useWorkspaceGroups: () => groups,
}));

vi.mock("@/stores/conversation", async () => {
  const actual = await vi.importActual<typeof import("@/stores/conversation")>(
    "@/stores/conversation",
  );
  return {
    ...actual,
    useConversationStore: (
      sel: (s: { currentConversationId: string | null }) => unknown,
    ) => sel({ currentConversationId: null }),
  };
});

vi.mock("@/stores/sidebar", () => ({
  useSidebarStore: (
    sel: (s: {
      expandedSections: Record<string, boolean>;
      setSection: () => void;
      folderGroupOrder: string[];
      reorderFolderGroups: (nextVisibleIds: string[]) => void;
    }) => unknown,
  ) =>
    sel({
      // Auto-expand every group in tests so unpinned rows are visible.
      expandedSections: { f1: true },
      setSection: vi.fn(),
      folderGroupOrder: [],
      reorderFolderGroups: vi.fn(),
    }),
}));

vi.mock("../ConversationItem", () => ({
  ConversationItem: ({ conversation }: { conversation: Conversation }) => (
    <div data-testid={`conv-${conversation.id}`}>{conversation.title}</div>
  ),
}));

vi.mock("../WorkspaceGroupHeader", () => ({
  WorkspaceGroupHeader: ({
    folder,
  }: {
    folder: { id: string; name: string };
  }) => <div data-testid={`group-${folder.id}`}>{folder.name}</div>,
}));

function makeConv(
  id: string,
  opts: { folderId?: string | null; pinned?: boolean; at?: string } = {},
): Conversation {
  return {
    id,
    title: id,
    updatedAt: opts.at ?? "2026-06-01T00:00:00Z",
    messageCount: 0,
    lastMessagePreview: null,
    folderId: opts.folderId ?? null,
    pinned: opts.pinned,
  };
}

function renderRail() {
  return render(
    <MemoryRouter>
      <PinnedConversations />
      <WorkspaceGroups />
      <RecentConversations />
    </MemoryRouter>,
  );
}

describe("sidebar rail 方案C (置顶 → 项目 → 裸聊)", () => {
  beforeEach(() => {
    convs.length = 0;
    groups = [];
  });

  afterEach(() => cleanup());

  it("lifts pinned bare + foldered chats above groups; no duplicates", () => {
    const pinnedBare = makeConv("pin-bare", {
      pinned: true,
      at: "2026-06-02T00:00:00Z",
    });
    const pinnedFolder = makeConv("pin-proj", {
      pinned: true,
      folderId: "f1",
      at: "2026-06-01T00:00:00Z",
    });
    const unpinnedFolder = makeConv("in-proj", { folderId: "f1" });
    const bare = makeConv("bare");
    convs.push(pinnedBare, pinnedFolder, unpinnedFolder, bare);
    groups = [
      {
        folder: {
          id: "f1",
          name: "Proj",
          mode: "cloud",
          localRootId: null,
          localSubpath: null,
        },
        convs: [pinnedFolder, unpinnedFolder],
        latest: Date.parse("2026-06-01T00:00:00Z"),
      },
    ];

    renderRail();

    const order = [...screen.getAllByTestId(/^conv-|^group-/)].map((el) =>
      el.getAttribute("data-testid"),
    );
    expect(order).toEqual([
      "conv-pin-bare",
      "conv-pin-proj",
      "group-f1",
      "conv-in-proj",
      "conv-bare",
    ]);
    expect(screen.getAllByTestId("conv-pin-proj")).toHaveLength(1);
  });

  it("omits pin zone when nothing is pinned", () => {
    convs.push(makeConv("bare"));
    renderRail();
    expect(screen.getByTestId("conv-bare")).toBeTruthy();
    expect(screen.queryByTestId("conv-pin-bare")).toBeNull();
  });
});
