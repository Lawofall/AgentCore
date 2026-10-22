// @vitest-environment jsdom
import { FileWorkbench } from "@/components/files/FileWorkbench";
import { TooltipProvider } from "@/components/ui/tooltip";
import { uiSet } from "@/lib/uiStorage";
import type { FolderMeta } from "@/services/folders";
import { useFoldersStore } from "@/stores/folders";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  folders: [] as FolderMeta[],
}));

const { createFolderMutate } = vi.hoisted(() => ({
  createFolderMutate: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [],
  getConversations: () => [],
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => state.folders,
  getFolders: () => state.folders,
  useCreateFolder: () => ({
    mutateAsync: createFolderMutate,
    isPending: false,
  }),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

vi.mock("@/components/folders/PendingFolderInvites", () => ({
  PendingFolderInvites: () => null,
}));

vi.mock("@/components/files/fileWorkbench/WorkspaceSection", () => ({
  WorkspaceSection: ({
    ws,
    nested,
    expanded,
    flashing,
  }: {
    ws: { name: string; wsId: string };
    nested?: ReactNode;
    expanded: boolean;
    flashing: boolean;
  }) => (
    <div
      data-testid={ws.wsId}
      data-expanded={expanded ? "1" : "0"}
      data-flashing={flashing ? "1" : "0"}
    >
      {ws.name}
      {expanded ? nested : null}
    </div>
  ),
}));

function untitledFolder(
  over: Partial<FolderMeta> & Pick<FolderMeta, "id" | "name">,
): FolderMeta {
  return {
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
    relPath: over.name,
    parentRelPath: "",
    ...over,
  };
}

function renderHub() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <FileWorkbench
          workspaces={[]}
          isLoading={false}
          isError={false}
          onRetry={() => {}}
          fsAvailable={false}
        />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

describe("FileWorkbench · 我的文件空态", () => {
  afterEach(() => {
    state.folders = [];
    uiSet("files-ws-expanded", []);
    createFolderMutate.mockReset();
    useFoldersStore.setState({
      pendingUntitledCreate: null,
      untitledCreateBusy: false,
      pendingRevealFolderId: null,
      pendingRenameFolderId: null,
    });
    cleanup();
  });

  it("empty 我的文件 offers 新建文件夹 on the zone header and empty state", async () => {
    createFolderMutate.mockImplementation(async () => {
      const folder = untitledFolder({ id: "n1", name: "未命名文件夹" });
      state.folders = [folder];
      return { folder, created: true };
    });
    renderHub();
    expect(screen.getByText("我的文件")).toBeTruthy();
    expect(screen.getByText("还没有文件夹")).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: "新建文件夹" });
    expect(buttons.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(buttons[0]);
    await waitFor(() => {
      expect(createFolderMutate).toHaveBeenCalledWith({
        name: "未命名文件夹",
        mode: "cloud",
        parentId: null,
      });
    });
  });

  it("command-palette untitled request still POSTs 未命名文件夹 and enters rename", async () => {
    createFolderMutate.mockImplementation(async () => {
      const folder = untitledFolder({ id: "n1", name: "未命名文件夹" });
      state.folders = [folder];
      return { folder, created: true };
    });
    renderHub();
    act(() => {
      useFoldersStore.getState().requestUntitledCloudFolder();
    });

    await waitFor(() => {
      expect(createFolderMutate).toHaveBeenCalledWith({
        name: "未命名文件夹",
        mode: "cloud",
        parentId: null,
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("folder:n1")).toBeTruthy();
    });
    expect(useFoldersStore.getState().pendingRenameFolderId).toBe("n1");
    expect(screen.queryByText("还没有文件夹")).toBeNull();
  });

  it("nested untitled request POSTs parentId", async () => {
    createFolderMutate.mockImplementation(async () => {
      const folder = untitledFolder({
        id: "n2",
        name: "未命名文件夹",
        relPath: "设计/未命名文件夹",
        parentRelPath: "设计",
      });
      state.folders = [
        untitledFolder({
          id: "p",
          name: "设计",
          relPath: "设计",
          parentRelPath: "",
        }),
        folder,
      ];
      return { folder, created: true };
    });
    renderHub();
    act(() => {
      useFoldersStore.getState().requestUntitledCloudFolder("p");
    });
    await waitFor(() => {
      expect(createFolderMutate).toHaveBeenCalledWith({
        name: "未命名文件夹",
        mode: "cloud",
        parentId: "p",
      });
    });
  });

  it("reveals a nested folder by expanding ancestors so the new row is visible", async () => {
    state.folders = [
      untitledFolder({
        id: "p",
        name: "设计",
        relPath: "设计",
        parentRelPath: "",
      }),
      untitledFolder({
        id: "c",
        name: "图标",
        relPath: "设计/图标",
        parentRelPath: "设计",
      }),
    ];
    renderHub();
    expect(screen.getByTestId("folder:p")).toBeTruthy();
    expect(screen.queryByTestId("folder:c")).toBeNull();

    act(() => {
      useFoldersStore.getState().revealCreatedFolder("c", { rename: true });
    });

    await waitFor(() => {
      expect(screen.getByTestId("folder:c")).toBeTruthy();
    });
    expect(screen.getByTestId("folder:p").getAttribute("data-expanded")).toBe(
      "1",
    );
    expect(screen.getByTestId("folder:c").getAttribute("data-flashing")).toBe(
      "1",
    );
  });
});
