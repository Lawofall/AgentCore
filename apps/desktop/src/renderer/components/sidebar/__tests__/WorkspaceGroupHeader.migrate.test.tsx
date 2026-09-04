// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { LOCAL_TRADITIONAL_LABEL } from "@/lib/conversationWorkspaceMode";
import type { FolderMeta } from "@/services/folders";
import { useFoldersStore } from "@/stores/folders";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceGroupHeader } from "../WorkspaceGroupHeader";

vi.mock("@/components/folders/FolderMembersDialog", () => ({
  FolderMembersDialog: () => null,
}));
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

function folder(
  mode: "local" | "cloud",
  overrides: Partial<FolderMeta> = {},
): FolderMeta {
  return {
    id: "f1",
    name: "DemoProj",
    mode,
    localRootId: mode === "local" ? "root-1" : null,
    localSubpath: mode === "local" ? "" : null,
    ...overrides,
  };
}

function renderHeader(
  mode: "local" | "cloud",
  overrides: Partial<FolderMeta> = {},
) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <WorkspaceGroupHeader
          folder={folder(mode, overrides)}
          convs={[]}
          expanded
          onToggleExpanded={() => {}}
        />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useFoldersStore.setState({
    importToCloudOpen: false,
    importToCloudPrefill: null,
  });
});

afterEach(() => {
  cleanup();
});

describe("WorkspaceGroupHeader · local traditional (no migrate debt badge)", () => {
  it("local group shows 本机传统 icon, no 请迁移 badge", () => {
    renderHeader("local");
    expect(screen.getByText("DemoProj")).toBeTruthy();
    expect(screen.getByLabelText(LOCAL_TRADITIONAL_LABEL)).toBeTruthy();
    expect(screen.queryByText("请迁移")).toBeNull();
    expect(screen.getByLabelText("在此本机文件夹中新开对话")).toBeTruthy();
  });

  it("cloud group has no import menu entry", async () => {
    renderHeader("cloud");
    expect(screen.getByText("DemoProj")).toBeTruthy();
    expect(screen.queryByText("请迁移")).toBeNull();
    expect(screen.getByLabelText("云端")).toBeTruthy();
    expect(screen.getByLabelText("在此文件夹中新开对话")).toBeTruthy();

    const trigger = screen.getByLabelText("文件夹操作");
    fireEvent.pointerDown(trigger);
    fireEvent.click(trigger);
    expect(await screen.findByText("新建对话")).toBeTruthy();
    expect(screen.queryByText("导入到「我的文件」")).toBeNull();
  });

  it("⋯ menu 导入到「我的文件」 opens import with prefill", async () => {
    renderHeader("local");
    const trigger = screen.getByLabelText("文件夹操作");
    fireEvent.pointerDown(trigger);
    fireEvent.click(trigger);
    const item = await screen.findByText("导入到「我的文件」");
    fireEvent.click(item);
    const state = useFoldersStore.getState();
    expect(state.importToCloudOpen).toBe(true);
    expect(state.importToCloudPrefill).toEqual({
      rootId: "root-1",
      folderName: "DemoProj",
    });
  });

  it("a nested folder shows its ancestor path so same-named siblings differ", () => {
    renderHeader("cloud", { relPath: "设计/图标", parentRelPath: "设计" });
    expect(screen.getByText("DemoProj")).toBeTruthy();
    expect(screen.getByText("设计")).toBeTruthy();
  });

  it("cloud group menu has 成员; local does not", async () => {
    renderHeader("cloud");
    fireEvent.pointerDown(screen.getByLabelText("文件夹操作"));
    fireEvent.click(screen.getByLabelText("文件夹操作"));
    expect(await screen.findByText("成员")).toBeTruthy();

    cleanup();
    renderHeader("local");
    fireEvent.pointerDown(screen.getByLabelText("文件夹操作"));
    fireEvent.click(screen.getByLabelText("文件夹操作"));
    expect(await screen.findByText("导入到「我的文件」")).toBeTruthy();
    expect(screen.queryByText("成员")).toBeNull();
  });
});
