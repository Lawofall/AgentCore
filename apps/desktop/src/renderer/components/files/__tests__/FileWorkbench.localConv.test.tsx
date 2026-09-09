// @vitest-environment jsdom
import { FileWorkbench } from "@/components/files/FileWorkbench";
import { scratchHasUserVisibleFiles } from "@/components/files/fileWorkbench/localConvScratch";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode } from "@/lib/fileSource";
import type { FolderMeta } from "@/services/folders";
import type { WorkspaceInfo } from "@/services/workspaces";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  conversations: [] as { id: string; title: string; updatedAt: string }[],
  folders: [] as FolderMeta[],
  listDirByWs: {} as Record<string, FileNode[]>,
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => state.hasLocalFiles(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => state.conversations,
  getConversations: () => state.conversations,
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => state.folders,
  getFolders: () => state.folders,
}));

vi.mock("@/components/folders/PendingFolderInvites", () => ({
  PendingFolderInvites: () => null,
}));

vi.mock("@/components/files/fileWorkbench/WorkspaceSection", () => ({
  WorkspaceSection: ({ ws }: { ws: { name: string; wsId: string } }) => (
    <div data-testid={ws.wsId}>{ws.name}</div>
  ),
}));

const localConv = (over: Partial<WorkspaceInfo> = {}): WorkspaceInfo => ({
  wsId: "conv:c1",
  name: "未命名对话",
  location: "local",
  rootId: "r1",
  subpath: "conversations/c1",
  hasFiles: true,
  ...over,
});

function renderHub(
  workspaces: WorkspaceInfo[],
  over: { fsAvailable?: boolean } = {},
) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <FileWorkbench
          workspaces={workspaces}
          isLoading={false}
          isError={false}
          onRetry={() => {}}
          fsAvailable={over.fsAvailable ?? true}
          probeLocalScratchHasFiles={async (ws) =>
            scratchHasUserVisibleFiles({
              listDir: async () => state.listDirByWs[ws.wsId] ?? [],
            })
          }
        />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

describe("FileWorkbench · local conv: desks", () => {
  afterEach(() => {
    state.conversations = [];
    state.folders = [];
    state.listDirByWs = {};
    state.hasLocalFiles.mockReturnValue(true);
    cleanup();
  });

  it("lists a local conv: with user files under 本机文件夹 using the sidebar title", async () => {
    state.conversations = [
      { id: "c1", title: "定价讨论", updatedAt: "2026-06-01T00:00:00Z" },
    ];
    state.listDirByWs["conv:c1"] = [
      { path: "notes.md", name: "notes.md", isDir: false },
    ];
    renderHub([
      localConv(),
      {
        wsId: "conv:cloud1",
        name: "一次快速对话",
        location: "cloud",
        rootId: null,
        subpath: "",
        hasFiles: true,
      },
    ]);

    expect((await screen.findByTestId("conv:c1")).textContent).toBe("定价讨论");
    expect(screen.getByText("本机文件夹")).toBeTruthy();
    expect(screen.getByText("我的文件")).toBeTruthy();
    expect(screen.queryByTestId("conv:cloud1")).toBeNull();
    expect(screen.queryByText("一次快速对话")).toBeNull();
    expect(screen.queryByText("快速对话")).toBeNull();
    expect(screen.queryByText("打开过的本机文件夹会出现在这里")).toBeNull();
  });

  it("omits a local conv: whose tree is empty or internals-only, even when hasFiles is true", async () => {
    state.listDirByWs["conv:c1"] = [
      { path: "AgentCore", name: "AgentCore", isDir: true },
      { path: ".agentcore", name: ".agentcore", isDir: true },
      { path: "attachments", name: "attachments", isDir: true },
    ];
    renderHub([localConv({ name: "定价讨论" })]);

    await waitFor(() => {
      expect(screen.queryByTestId("conv:c1")).toBeNull();
      expect(screen.getByText("还没有文件夹")).toBeTruthy();
    });
    expect(screen.queryByText("定价讨论")).toBeNull();
  });

  it("labels a local conv: without a sidebar title as 对话文件", async () => {
    state.listDirByWs["conv:c1"] = [
      { path: "out.md", name: "out.md", isDir: false },
    ];
    renderHub([localConv({ name: "未命名对话" })]);

    expect((await screen.findByTestId("conv:c1")).textContent).toBe("对话文件");
    expect(screen.queryByText("未命名对话")).toBeNull();
    expect(screen.queryByText("云文件夹")).toBeNull();
  });

  it("mixes a local conv: desk with opened folders and skips the empty copy", async () => {
    state.folders = [
      {
        id: "f-local",
        name: "仓库",
        mode: "local",
        localRootId: "r1",
        localSubpath: null,
      },
    ];
    state.conversations = [
      { id: "c1", title: "定价讨论", updatedAt: "2026-06-01T00:00:00Z" },
    ];
    state.listDirByWs["conv:c1"] = [
      { path: "notes.md", name: "notes.md", isDir: false },
    ];
    renderHub([localConv()]);

    expect((await screen.findByTestId("conv:c1")).textContent).toBe("定价讨论");
    expect(screen.getByTestId("folder:f-local").textContent).toBe("仓库");
    expect(screen.getByText("本机文件夹")).toBeTruthy();
    expect(screen.queryByText("打开过的本机文件夹会出现在这里")).toBeNull();
  });
});
