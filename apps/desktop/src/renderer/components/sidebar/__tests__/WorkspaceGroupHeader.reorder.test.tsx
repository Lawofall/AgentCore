// @vitest-environment jsdom
import { TAB_DRAG_THRESHOLD_PX, useSortableTabIds } from "@/components/ui";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FolderMeta } from "@/services/folders";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { WorkspaceGroupHeader } from "../WorkspaceGroupHeader";

vi.mock("@/hooks/useConversations", () => ({
  useArchiveConversation: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/hooks/useFolders", () => ({
  useDeleteFolder: () => ({ mutate: vi.fn() }),
  usePermanentDeleteFolder: () => ({ mutate: vi.fn() }),
  useRestoreFolder: () => ({ mutate: vi.fn() }),
  useFolderTrash: () => ({ data: undefined }),
  releaseFolderConversations: vi.fn(() => false),
}));
vi.mock("@/lib/newConversation", () => ({
  startNewConversation: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));
vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: {
      currentConversationId: null;
      dropConversationRuntime: () => void;
    }) => unknown,
  ) =>
    sel({
      currentConversationId: null,
      dropConversationRuntime: vi.fn(),
    }),
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

afterEach(cleanup);

function folder(id: string, name: string): FolderMeta {
  return {
    id,
    name,
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
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

function HeaderHarness({
  onReorder,
  onToggle,
}: {
  onReorder?: (ids: string[]) => void;
  onToggle?: (id: string) => void;
}) {
  const { getItemProps } = useSortableTabIds(
    ["f1", "f2"],
    (next) => onReorder?.(next),
    { axis: "y", idleGrabCursor: false },
  );
  return (
    <MemoryRouter>
      <TooltipProvider>
        <WorkspaceGroupHeader
          folder={folder("f1", "Alpha")}
          convs={[]}
          expanded
          onToggleExpanded={() => onToggle?.("f1")}
          sortable={getItemProps("f1")}
        />
        <WorkspaceGroupHeader
          folder={folder("f2", "Beta")}
          convs={[]}
          expanded
          onToggleExpanded={() => onToggle?.("f2")}
          sortable={getItemProps("f2")}
        />
      </TooltipProvider>
    </MemoryRouter>
  );
}

describe("WorkspaceGroupHeader · folder-group reorder", () => {
  it("idle header has no grab cursor; + and ⋯ are no-drag", () => {
    render(<HeaderHarness />);
    const row = screen.getByText("Alpha").closest("[data-tab-id]");
    expect(row?.classList.contains("cursor-grab")).toBe(false);
    for (const el of screen.getAllByLabelText("文件夹操作")) {
      expect(el.closest("[data-no-tab-drag]")).toBeTruthy();
    }
    for (const el of screen.getAllByLabelText("在此文件夹中新开对话")) {
      expect(el.closest("[data-no-tab-drag]")).toBeTruthy();
    }
  });

  it("click below threshold still toggles expand", () => {
    const onToggle = vi.fn();
    const onReorder = vi.fn();
    render(<HeaderHarness onToggle={onToggle} onReorder={onReorder} />);
    const name = screen.getByText("Alpha");
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
    expect(onToggle).toHaveBeenCalledWith("f1");
    expect(onReorder).not.toHaveBeenCalled();
  });

  it("does not start a drag from + or ⋯", () => {
    const onReorder = vi.fn();
    render(<HeaderHarness onReorder={onReorder} />);
    for (const label of ["文件夹操作", "在此文件夹中新开对话"] as const) {
      for (const chrome of screen.getAllByLabelText(label)) {
        fireEvent.pointerDown(chrome, {
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
      }
    }
    expect(onReorder).not.toHaveBeenCalled();
  });

  it("drag past threshold reorders and swallows the trailing click", () => {
    const onToggle = vi.fn();
    const onReorder = vi.fn();
    render(<HeaderHarness onToggle={onToggle} onReorder={onReorder} />);
    const a = screen.getByText("Alpha").closest("[data-tab-id]");
    const b = screen.getByText("Beta").closest("[data-tab-id]");
    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    mockRect(a as Element, 0);
    mockRect(b as Element, 40);
    document.elementsFromPoint = () => [b as Element];
    fireEvent.pointerDown(screen.getByText("Alpha"), {
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
    fireEvent.pointerUp(document, {
      clientX: 10,
      clientY: 64,
      pointerId: 1,
    });
    fireEvent.click(screen.getByText("Alpha"));
    expect(onReorder).toHaveBeenCalledWith(["f2", "f1"]);
    expect(onToggle).not.toHaveBeenCalled();
  });
});
