// @vitest-environment jsdom
import { TAB_DRAG_THRESHOLD_PX } from "@/components/ui";
import type { Conversation } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { HTMLAttributes } from "react";
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
const setSection = vi.fn();
const reorderFolderGroups = vi.fn();

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => convs,
}));

vi.mock("@/hooks/useWorkspaceGroups", () => ({
  useWorkspaceGroups: () => groups,
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
      expandedSections: { f1: true, f2: true },
      setSection,
      folderGroupOrder: [],
      reorderFolderGroups,
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
    expanded,
    onToggleExpanded,
    sortable,
  }: {
    folder: { id: string; name: string };
    expanded: boolean;
    onToggleExpanded: () => void;
    sortable?: HTMLAttributes<HTMLDivElement>;
  }) => (
    <div data-testid={`group-${folder.id}`} {...sortable}>
      <button type="button" onClick={onToggleExpanded}>
        {folder.name}
      </button>
      <span data-testid={`group-${folder.id}-expanded`}>
        {expanded ? "open" : "closed"}
      </span>
    </div>
  ),
}));

beforeAll(() => {
  Element.prototype.setPointerCapture ??= function setPointerCapture() {};
  Element.prototype.releasePointerCapture ??=
    function releasePointerCapture() {};
  Element.prototype.hasPointerCapture ??= function hasPointerCapture() {
    return false;
  };
  document.elementsFromPoint ??= () => [];
});

function makeConv(
  id: string,
  opts: { folderId?: string | null; pinned?: boolean } = {},
): Conversation {
  return {
    id,
    title: id,
    updatedAt: "2026-06-01T00:00:00Z",
    messageCount: 0,
    lastMessagePreview: null,
    folderId: opts.folderId ?? null,
    pinned: opts.pinned,
  };
}

function makeGroup(id: string, name: string, groupConvs: Conversation[]) {
  return {
    folder: {
      id,
      name,
      mode: "cloud" as const,
      localRootId: null,
      localSubpath: null,
    },
    convs: groupConvs,
    latest: Date.parse("2026-06-01T00:00:00Z"),
  };
}

function mockRect(el: Element, top: number) {
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: top,
    left: 0,
    top,
    width: 200,
    height: 32,
    right: 200,
    bottom: top + 32,
    toJSON() {},
  });
}

beforeEach(() => {
  convs.length = 0;
  groups = [];
  setSection.mockReset();
  reorderFolderGroups.mockReset();
});

afterEach(cleanup);

describe("WorkspaceGroups · folder-group reorder", () => {
  it("drag past threshold calls reorderFolderGroups with the visible ids", () => {
    const c1 = makeConv("c1", { folderId: "f1" });
    const c2 = makeConv("c2", { folderId: "f2" });
    convs.push(c1, c2);
    groups = [makeGroup("f1", "Alpha", [c1]), makeGroup("f2", "Beta", [c2])];

    render(
      <MemoryRouter>
        <WorkspaceGroups />
      </MemoryRouter>,
    );

    const a = screen.getByTestId("group-f1");
    const b = screen.getByTestId("group-f2");
    mockRect(a, 0);
    mockRect(b, 40);
    document.elementsFromPoint = () => [b];
    fireEvent.pointerDown(screen.getByRole("button", { name: "Alpha" }), {
      button: 0,
      clientX: 10,
      clientY: 8,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10,
      clientY: 8 + TAB_DRAG_THRESHOLD_PX + 1,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10,
      clientY: 64,
      pointerId: 1,
    });
    expect(screen.getByTestId("folder-group-insert")).toBeTruthy();
    expect(screen.getByTestId("folder-group-drag-ghost").textContent).toContain(
      "Alpha",
    );

    fireEvent.pointerUp(document, {
      clientX: 10,
      clientY: 64,
      pointerId: 1,
    });

    expect(reorderFolderGroups).toHaveBeenCalledWith(["f2", "f1"]);
    expect(screen.queryByTestId("folder-group-insert")).toBeNull();
    expect(screen.queryByTestId("folder-group-drag-ghost")).toBeNull();
  });

  it("click below threshold still toggles the group", () => {
    const c1 = makeConv("c1", { folderId: "f1" });
    const c2 = makeConv("c2", { folderId: "f2" });
    convs.push(c1, c2);
    groups = [makeGroup("f1", "Alpha", [c1]), makeGroup("f2", "Beta", [c2])];

    render(
      <MemoryRouter>
        <WorkspaceGroups />
      </MemoryRouter>,
    );

    const name = screen.getByRole("button", { name: "Alpha" });
    fireEvent.pointerDown(name, {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerUp(document, {
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.click(name);

    expect(setSection).toHaveBeenCalledWith("f1", false);
    expect(reorderFolderGroups).not.toHaveBeenCalled();
  });

  it("conversation rows do not start a group drag", () => {
    const c1 = makeConv("c1", { folderId: "f1" });
    const c2 = makeConv("c2", { folderId: "f2" });
    convs.push(c1, c2);
    groups = [makeGroup("f1", "Alpha", [c1]), makeGroup("f2", "Beta", [c2])];

    render(
      <MemoryRouter>
        <WorkspaceGroups />
      </MemoryRouter>,
    );

    fireEvent.pointerDown(screen.getByTestId("conv-c1"), {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10,
      clientY: 10 + TAB_DRAG_THRESHOLD_PX + 8,
      pointerId: 1,
    });
    fireEvent.pointerUp(document, {
      clientX: 10,
      clientY: 10 + TAB_DRAG_THRESHOLD_PX + 8,
      pointerId: 1,
    });

    expect(reorderFolderGroups).not.toHaveBeenCalled();
  });
});
