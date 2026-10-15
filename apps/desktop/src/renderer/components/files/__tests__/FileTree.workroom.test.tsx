// @vitest-environment jsdom

import { FileTree } from "@/components/files/FileTree";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTreeRowMenu", () => ({
  FileTreeRowMenu: () => null,
}));

function eagerSource(
  entries: FileNode[],
  id = "workspace:workroom",
): FileSource {
  return {
    id,
    label: "工作区",
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
    listTree: async () => entries,
    listDir: async () => {
      throw new Error("eager source");
    },
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
  };
}

function lazySource(
  listing: Record<string, FileNode[]>,
  id: string,
  extra: Partial<FileSource> = {},
): FileSource {
  return {
    id,
    label: "本机",
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
    listDir: async (dir) => listing[dir] ?? [],
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
    ...extra,
  };
}

function renderTree(
  source: FileSource,
  extra: {
    renderWorkroomLead?: () => import("react").ReactNode;
    onCreateWorkroomEntry?: () => boolean | Promise<boolean>;
    filterQuery?: string;
  } = {},
) {
  return render(
    <TooltipProvider>
      <FileTree source={source} onOpenFile={() => {}} {...extra} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe("FileTree 文件夹条目", () => {
  it("盘上 GAgent 用真名，展开后才见子项", async () => {
    const src = eagerSource([
      { path: "报告.md", name: "报告.md", isDir: false },
      { path: "GAgent", name: "GAgent", isDir: true },
      { path: "GAgent/文档", name: "文档", isDir: true },
      { path: "GAgent/文档/工作稿", name: "工作稿", isDir: true },
      {
        path: "GAgent/文档/已迁入记忆",
        name: "已迁入记忆",
        isDir: true,
      },
      {
        path: "GAgent/文档/工作稿/初稿.md",
        name: "初稿.md",
        isDir: false,
      },
    ]);
    renderTree(src);

    expect(await screen.findByText("GAgent")).toBeTruthy();
    expect(screen.queryByText(".gagent")).toBeNull();
    expect(screen.queryByText("文档")).toBeNull();

    fireEvent.click(screen.getByText("GAgent"));
    expect(await screen.findByText("文档")).toBeTruthy();
    expect(screen.queryByText("工作稿")).toBeNull();

    fireEvent.click(screen.getByText("文档"));
    expect(await screen.findByText("工作稿")).toBeTruthy();
  });

  it("条目直接列在这一层，不套虚拟目录", async () => {
    const src = eagerSource(
      [{ path: "报告.md", name: "报告.md", isDir: false }],
      "workspace:workroom-entries",
    );
    renderTree(src, { renderWorkroomLead: () => <div>画像.md</div> });

    expect(await screen.findByText("画像.md")).toBeTruthy();
    expect(screen.getByText("报告.md")).toBeTruthy();
    expect(screen.queryByText(".gagent")).toBeNull();
  });

  it("有条目时，其它空目录仍显示空文件夹", async () => {
    const src = eagerSource(
      [
        { path: "空目录", name: "空目录", isDir: true },
        { path: "报告.md", name: "报告.md", isDir: false },
      ],
      "workspace:workroom-empty-hint",
    );
    renderTree(src, { renderWorkroomLead: () => <div>画像.md</div> });

    expect(await screen.findByText("画像.md")).toBeTruthy();
    fireEvent.click(screen.getByText("空目录"));
    expect(await screen.findByText("空文件夹")).toBeTruthy();
    expect(screen.getByText("报告.md")).toBeTruthy();
  });

  it("全选只数真实文件", async () => {
    const src = eagerSource(
      [
        { path: "报告.md", name: "报告.md", isDir: false },
        { path: "附录.md", name: "附录.md", isDir: false },
      ],
      "workspace:workroom-select-all",
    );
    renderTree(src, { renderWorkroomLead: () => <div>画像.md</div> });

    fireEvent.keyDown(await screen.findByText("报告.md"), {
      key: "a",
      ctrlKey: true,
    });
    expect(screen.getByText("已选择 2 项")).toBeTruthy();
  });

  it("新建条目按钮可见，并调用回调", async () => {
    const src = eagerSource(
      [{ path: "报告.md", name: "报告.md", isDir: false }],
      "workspace:workroom-create",
    );
    const onCreate = vi.fn(async () => true);
    renderTree(src, {
      renderWorkroomLead: () => <div>画像.md</div>,
      onCreateWorkroomEntry: onCreate,
    });

    fireEvent.click(await screen.findByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalled());
    expect(screen.getByText("画像.md")).toBeTruthy();
  });

  it("展开 GAgent 时 watch 覆盖该目录", async () => {
    const watched: string[] = [];
    const src = lazySource(
      {
        "": [
          { path: "GAgent", name: "GAgent", isDir: true },
          { path: "报告.md", name: "报告.md", isDir: false },
        ],
        GAgent: [{ path: "GAgent/文档", name: "文档", isDir: true }],
      },
      "local:workroom-watch-docs",
      {
        caps: { watch: true, transfer: false, edit: false, snapshots: false },
        watch: (dir) => {
          watched.push(dir);
          return () => {};
        },
      },
    );
    renderTree(src);
    fireEvent.click(await screen.findByText("GAgent"));
    await waitFor(() => {
      expect(watched).toContain("GAgent");
    });
  });
});
