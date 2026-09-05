// @vitest-environment jsdom
/**
 * 「我的文件」里云端文件夹的三件能力入口——版本 / 软删区 / 导出 ZIP。
 *
 * 这三件此前只有对话右坞才有，从文件页进来的用户得绕回某个对话才够得着。这里钉的是
 * 入口的**门控**（后端对本机工作区一律 409，不能让用户点进一个必然失败的
 * 动作）与**出口**（版本 / 软删区走开标签页那条缝，导出直接触发打包下载）。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileSource } from "@/lib/fileSource";
import type { FolderMeta } from "@/services/folders";
import type { WorkspaceInfo } from "@/services/workspaces";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getFoldersMock } = vi.hoisted(() => ({
  getFoldersMock: vi.fn((): FolderMeta[] => []),
}));

vi.mock("@/components/files/FileTree", () => ({
  FileTree: () => <div data-testid="tree" />,
}));

vi.mock("@/components/folders/FolderMembersDialog", () => ({
  FolderMembersDialog: () => null,
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: "/files" }),
  Link: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [],
  useDeleteConversation: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useRenameConversation: () => ({ mutate: vi.fn() }),
  useRestoreConversation: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => getFoldersMock(),
  releaseFolderConversations: vi.fn(),
  useDeleteFolder: () => ({ mutateAsync: vi.fn() }),
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
    sel({ currentConversationId: null, dropConversationRuntime: vi.fn() }),
}));

vi.mock("@/services/workspaces", () => ({ wsExportZip: vi.fn() }));

import { wsExportZip } from "@/services/workspaces";
import { WorkspaceSection } from "../WorkspaceSection";

function source(over: Partial<FileSource["caps"]> = {}): FileSource {
  return {
    id: "workspace:test",
    label: "工作区",
    caps: {
      watch: false,
      transfer: true,
      edit: true,
      snapshots: true,
      ...over,
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

function renderSection(opts: {
  ws?: WorkspaceInfo;
  source?: FileSource | null;
  onOpenFile?: (path: string, name: string) => void;
}) {
  return render(
    <TooltipProvider>
      <WorkspaceSection
        ws={opts.ws ?? ws()}
        source={opts.source === undefined ? source() : opts.source}
        activePath={null}
        expanded={false}
        onToggle={() => {}}
        onOpenFile={opts.onOpenFile ?? (() => {})}
        flashing={false}
      />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  getFoldersMock.mockReturnValue([]);
});

describe("云端文件夹的版本 / 软删区 / 导出入口", () => {
  it("版本与软删区各开一个带工作区名的标签页", async () => {
    const onOpenFile = vi.fn();
    const { unmount } = renderSection({ onOpenFile });

    fireEvent.contextMenu(screen.getByText("季度报告"));
    fireEvent.click(await screen.findByText("版本…"));
    expect(onOpenFile).toHaveBeenCalledWith(
      "__ws_versions__",
      "版本 · 季度报告",
    );

    unmount();
    onOpenFile.mockClear();
    renderSection({ onOpenFile });

    fireEvent.contextMenu(screen.getByText("季度报告"));
    fireEvent.click(await screen.findByText("软删区…"));
    expect(onOpenFile).toHaveBeenCalledWith(
      "__ws_trash__",
      "软删区 · 季度报告",
    );
  });

  it("导出 ZIP 打包整个工作区", async () => {
    renderSection({});

    fireEvent.contextMenu(screen.getByText("季度报告"));
    fireEvent.click(await screen.findByText("导出 ZIP"));

    await waitFor(() => expect(wsExportZip).toHaveBeenCalledWith("folder:f1"));
  });

  it("本机文件夹不挂这三项：它的版本与回收站是另一条轨", async () => {
    renderSection({
      ws: ws({ wsId: "folder:f2", location: "local", rootId: "root-1" }),
      source: source({ snapshots: false }),
    });

    fireEvent.contextMenu(screen.getByText("季度报告"));
    await screen.findByText("新建文件");
    expect(screen.queryByText("版本…")).toBeNull();
    expect(screen.queryByText("软删区…")).toBeNull();
    expect(screen.queryByText("导出 ZIP")).toBeNull();
  });

  it("只读协作桌不挂：viewer 源无 snapshots", async () => {
    renderSection({
      ws: ws({ wsId: "folder:f-view" }),
      source: source({ snapshots: false }),
    });

    fireEvent.contextMenu(screen.getByText("季度报告"));
    await screen.findByText("新建文件");
    expect(screen.queryByText("版本…")).toBeNull();
    expect(screen.queryByText("导出 ZIP")).toBeNull();
  });

  it("离线时不挂：云端这三件都要联网", async () => {
    render(
      <TooltipProvider>
        <WorkspaceSection
          ws={ws()}
          source={source()}
          activePath={null}
          expanded={false}
          onToggle={() => {}}
          onOpenFile={() => {}}
          flashing={false}
          offlineCloud
        />
      </TooltipProvider>,
    );

    fireEvent.contextMenu(screen.getByText("季度报告"));
    await waitFor(() => expect(screen.queryByText("版本…")).toBeNull());
    expect(screen.queryByText("导出 ZIP")).toBeNull();
  });
});

describe("云端文件夹的协作人图标", () => {
  it("owner desk with a roster shows 协作 · N 人, never 已共享", () => {
    getFoldersMock.mockReturnValue([
      {
        id: "f1",
        name: "季度报告",
        mode: "cloud",
        localRootId: null,
        localSubpath: null,
        myRole: "owner",
        collaboratorCount: 2,
      },
    ]);
    renderSection({});
    expect(screen.getByText("协作 · 2 人")).toBeTruthy();
    expect(screen.queryByText("已共享")).toBeNull();
  });

  it("member desk shows 可编辑, not the people mark", () => {
    getFoldersMock.mockReturnValue([
      {
        id: "f1",
        name: "季度报告",
        mode: "cloud",
        localRootId: null,
        localSubpath: null,
        myRole: "editor",
        collaboratorCount: 2,
      },
    ]);
    renderSection({});
    expect(screen.getByText("可编辑")).toBeTruthy();
    expect(screen.queryByLabelText(/协作 ·/)).toBeNull();
  });
});
