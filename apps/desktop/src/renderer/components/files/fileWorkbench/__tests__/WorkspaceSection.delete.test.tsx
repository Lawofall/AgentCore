// @vitest-environment jsdom
/**
 * 文件中枢工作区轨：对话根右键「删除对话」一键软删；文件夹根右键「删除文件夹…」
 * 走与侧栏同一确认（所有者才有；树内文件「删除」不是这条）。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileSource } from "@/lib/fileSource";
import type { WorkspaceInfo } from "@/services/workspaces";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  deleteConversation: vi.fn(),
  restoreConversation: vi.fn(),
  notifyConversationDeleted: vi.fn(),
  dropConversationRuntime: vi.fn(),
  deleteFolder: vi.fn(),
  notifyInfo: vi.fn(),
  navigate: vi.fn(),
  folderRole: "owner" as "owner" | "editor" | "viewer",
  folderName: "季度报告",
}));

vi.mock("@/components/files/FileTree", () => ({
  FileTree: () => <div data-testid="tree" />,
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => mocks.navigate,
  useLocation: () => ({ pathname: "/files" }),
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
  getFolders: () => [
    {
      id: "f1",
      name: mocks.folderName,
      mode: "cloud",
      localRootId: null,
      localSubpath: null,
      myRole: mocks.folderRole,
    },
  ],
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

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyActionError: vi.fn(),
  notifyInfo: mocks.notifyInfo,
}));

import { useFoldersStore } from "@/stores/folders";
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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
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
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.deleteConversation.mockReset().mockResolvedValue(undefined);
  mocks.restoreConversation.mockReset();
  mocks.notifyConversationDeleted.mockReset();
  mocks.dropConversationRuntime.mockReset();
  mocks.deleteFolder.mockReset().mockResolvedValue(undefined);
  mocks.notifyInfo.mockReset();
  mocks.navigate.mockReset();
  mocks.folderRole = "owner";
  mocks.folderName = "季度报告";
  useFoldersStore.setState({
    pendingRevealFolderId: null,
    pendingRenameFolderId: null,
    pendingUntitledCreate: null,
    untitledCreateBusy: false,
  });
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
    expect(screen.queryByText("删除文件夹…")).toBeNull();
  });

  it("所有者右键文件夹根经确认后软删，点菜单本身不删", async () => {
    renderSection();

    fireEvent.contextMenu(screen.getByText("季度报告"));
    fireEvent.click(await screen.findByText("删除文件夹…"));
    expect(mocks.deleteFolder).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("删除文件夹「季度报告」？")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "删除文件夹" }));

    await waitFor(() => expect(mocks.deleteFolder).toHaveBeenCalledWith("f1"));
    expect(mocks.notifyInfo).toHaveBeenCalledWith(
      "已删除文件夹",
      expect.objectContaining({ description: "季度报告" }),
    );
    expect(mocks.deleteConversation).not.toHaveBeenCalled();
  });

  it("协作者右键文件夹根没有删除文件夹", async () => {
    mocks.folderRole = "editor";
    renderSection();

    fireEvent.contextMenu(screen.getByText("季度报告"));
    await screen.findByText("新建文件");
    expect(screen.getByText("在此新建文件夹")).toBeTruthy();
    expect(screen.queryByText("删除文件夹…")).toBeNull();
    expect(mocks.deleteFolder).not.toHaveBeenCalled();
  });

  it("第一次改名 Esc 且仍是占位名则软删空桌，不走确认框", async () => {
    mocks.folderName = "未命名文件夹";
    useFoldersStore.getState().revealCreatedFolder("f1", { rename: true });
    renderSection({ name: "未命名文件夹" });

    const input = await screen.findByDisplayValue("未命名文件夹");
    fireEvent.keyDown(input, { key: "Escape" });

    await waitFor(() => expect(mocks.deleteFolder).toHaveBeenCalledWith("f1"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mocks.notifyInfo).toHaveBeenCalledWith(
      "已删除文件夹",
      expect.objectContaining({ description: "未命名文件夹" }),
    );
  });

  it("普通重命名 Esc 不删文件夹", async () => {
    renderSection();
    fireEvent.contextMenu(screen.getByText("季度报告"));
    fireEvent.click(await screen.findByText("重命名"));
    const input = await screen.findByDisplayValue("季度报告");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(mocks.deleteFolder).not.toHaveBeenCalled();
  });
});
