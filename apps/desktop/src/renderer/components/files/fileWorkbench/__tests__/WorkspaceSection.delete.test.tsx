// @vitest-environment jsdom
/**
 * 文件中枢工作区轨：右键「删除对话」一键软删（撤销 toast），「删除文件夹」仍走弹窗。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileSource } from "@/lib/fileSource";
import type { WorkspaceInfo } from "@/services/workspaces";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  deleteConversation: vi.fn(),
  restoreConversation: vi.fn(),
  notifyConversationDeleted: vi.fn(),
  dropConversationRuntime: vi.fn(),
  deleteFolder: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@/components/files/FileTree", () => ({
  FileTree: () => <div data-testid="tree" />,
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => mocks.navigate,
  Link: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [],
  useDeleteConversation: () => ({
    mutate: vi.fn(),
    mutateAsync: mocks.deleteConversation,
  }),
  useRenameConversation: () => ({ mutate: vi.fn() }),
  useRestoreConversation: () => ({ mutate: mocks.restoreConversation }),
}));

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => [],
  releaseFolderConversations: vi.fn(),
  useDeleteFolder: () => ({ mutateAsync: mocks.deleteFolder }),
  useFolderTrash: () => ({ data: undefined }),
  usePermanentDeleteFolder: () => ({ mutate: vi.fn() }),
  useRestoreFolder: () => ({ mutate: vi.fn() }),
  useUpdateFolder: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/hooks/useWorkspaces", () => ({
  removeConversationScratch: vi.fn(),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationGenerating: () => false,
  useConversationStore: (sel: (s: unknown) => unknown) =>
    sel({
      currentConversationId: null,
      dropConversationRuntime: mocks.dropConversationRuntime,
    }),
}));

vi.mock("@/lib/conversationDeleteCopy", () => ({
  notifyConversationDeleted: mocks.notifyConversationDeleted,
}));

import { WorkspaceSection } from "../WorkspaceSection";

function source(): FileSource {
  return {
    id: "workspace:test",
    label: "工作区",
    caps: {
      watch: false,
      transfer: true,
      edit: true,
      snapshots: true,
    },
    listDir: async () => [],
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
  };
}

function ws(over: Partial<WorkspaceInfo> = {}): WorkspaceInfo {
  return {
    wsId: "folder:f1",
    name: "季度报告",
    location: "cloud",
    rootId: null,
    subpath: "",
    hasFiles: true,
    ...over,
  };
}

function renderSection(over: Partial<WorkspaceInfo> = {}) {
  return render(
    <TooltipProvider>
      <WorkspaceSection
        ws={ws(over)}
        source={source()}
        activePath={null}
        expanded={false}
        onToggle={() => {}}
        onOpenFile={() => {}}
        flashing={false}
      />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  mocks.deleteConversation.mockReset().mockResolvedValue(undefined);
  mocks.restoreConversation.mockReset();
  mocks.notifyConversationDeleted.mockReset();
  mocks.dropConversationRuntime.mockReset();
  mocks.deleteFolder.mockReset();
  mocks.navigate.mockReset();
});

afterEach(cleanup);

describe("工作区轨删除", () => {
  it("右键删除对话立即软删，不出现勾叉确认", async () => {
    renderSection({ wsId: "conv:c1", name: "定价讨论" });

    fireEvent.contextMenu(screen.getByText("定价讨论"));
    fireEvent.click(await screen.findByText("删除对话"));

    await waitFor(() =>
      expect(mocks.deleteConversation).toHaveBeenCalledWith("c1"),
    );
    expect(mocks.dropConversationRuntime).toHaveBeenCalledWith("c1");
    expect(mocks.notifyConversationDeleted).toHaveBeenCalledWith(
      "定价讨论",
      expect.any(Function),
    );
    expect(screen.queryByTitle("取消")).toBeNull();
    expect(screen.queryByText(/确认删除/)).toBeNull();
    expect(mocks.deleteFolder).not.toHaveBeenCalled();
  });

  it("右键删除文件夹仍打开确认弹窗，不立刻删", async () => {
    renderSection();

    fireEvent.contextMenu(screen.getByText("季度报告"));
    fireEvent.click(await screen.findByText("删除文件夹…"));

    expect(await screen.findByText("删除文件夹「季度报告」？")).toBeTruthy();
    expect(
      screen.getByText("立即永久清除全部对话与云端文件（不可恢复）"),
    ).toBeTruthy();
    expect(mocks.deleteFolder).not.toHaveBeenCalled();
    expect(mocks.deleteConversation).not.toHaveBeenCalled();
  });
});
