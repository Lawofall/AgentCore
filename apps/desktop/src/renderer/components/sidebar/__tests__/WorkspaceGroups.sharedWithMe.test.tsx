// @vitest-environment jsdom
import type { Conversation } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceGroups } from "../WorkspaceGroups";

const convs: Conversation[] = [];
const owned = [
  {
    folder: {
      id: "own-1",
      name: "我的项目",
      mode: "cloud" as const,
      localRootId: null,
      localSubpath: null,
    },
    convs: [] as Conversation[],
    latest: 1,
  },
];
const shared = [
  {
    folder: {
      id: "shared-1",
      name: "队友桌",
      mode: "cloud" as const,
      localRootId: null,
      localSubpath: null,
      myRole: "editor" as const,
    },
    convs: [] as Conversation[],
    latest: 2,
  },
];

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => convs,
}));

vi.mock("@/hooks/useWorkspaceGroups", () => ({
  useWorkspaceGroups: () => owned,
  useSharedWithMeWorkspaceGroups: () => shared,
}));

vi.mock("@/stores/aiAttention", () => ({
  useRequiredConversationIds: () => new Set<string>(),
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
      setSection: (id: string, expanded: boolean) => void;
      folderGroupOrder: string[];
      reorderFolderGroups: (nextVisibleIds: string[]) => void;
    }) => unknown,
  ) =>
    sel({
      expandedSections: { "own-1": true, "shared-1": true },
      setSection: vi.fn(),
      folderGroupOrder: [],
      reorderFolderGroups: vi.fn(),
    }),
}));

vi.mock("../ConversationItem", () => ({
  ConversationItem: () => null,
}));

vi.mock("../WorkspaceGroupHeader", () => ({
  WorkspaceGroupHeader: ({ folder }: { folder: { name: string } }) => (
    <div>{folder.name}</div>
  ),
}));

describe("WorkspaceGroups · 与我共享", () => {
  afterEach(cleanup);

  it("renders owned desks and a 与我共享 partition", () => {
    render(
      <MemoryRouter>
        <WorkspaceGroups />
      </MemoryRouter>,
    );
    expect(screen.getByText("我的项目")).toBeTruthy();
    expect(screen.getByText("与我共享")).toBeTruthy();
    expect(screen.getByText("队友桌")).toBeTruthy();
    expect(screen.queryByText("共享空间")).toBeNull();
  });
});
