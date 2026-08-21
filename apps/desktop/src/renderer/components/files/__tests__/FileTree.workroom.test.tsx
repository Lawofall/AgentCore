// @vitest-environment jsdom

import { FileTree } from "@/components/files/FileTree";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function renderTree(
  source: FileSource,
  extra: {
    renderWorkroomLead?: () => import("react").ReactNode;
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

describe("FileTree .agentcore 抽屉", () => {
  it("展开后露出工作稿而非 文档/ 壳，并隐藏迁移归档", async () => {
    const src = eagerSource([
      { path: "报告.md", name: "报告.md", isDir: false },
      { path: "AgentCore", name: "AgentCore", isDir: true },
      { path: "AgentCore/文档", name: "文档", isDir: true },
      { path: "AgentCore/文档/工作稿", name: "工作稿", isDir: true },
      {
        path: "AgentCore/文档/已迁入记忆",
        name: "已迁入记忆",
        isDir: true,
      },
      {
        path: "AgentCore/文档/工作稿/初稿.md",
        name: "初稿.md",
        isDir: false,
      },
    ]);
    renderTree(src);

    expect(await screen.findByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("文档")).toBeNull();
    expect(screen.queryByText("工作稿")).toBeNull();

    fireEvent.click(screen.getByText(".agentcore"));
    expect(await screen.findByText("工作稿")).toBeTruthy();
    expect(screen.queryByText("文档")).toBeNull();
    expect(screen.queryByText("已迁入记忆")).toBeNull();
  });

  it("有条目回调且盘上无 AgentCore 时仍挂虚拟 .agentcore，默认折叠", async () => {
    const src = eagerSource(
      [{ path: "报告.md", name: "报告.md", isDir: false }],
      "workspace:workroom-virtual",
    );
    renderTree(src, { renderWorkroomLead: () => <div>画像.md</div> });

    expect(await screen.findByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("画像.md")).toBeNull();
    fireEvent.click(screen.getByText(".agentcore"));
    expect(await screen.findByText("画像.md")).toBeTruthy();
    expect(screen.queryByText("空文件夹")).toBeNull();
  });

  it("有条目回调时，其它空目录仍显示空文件夹，不关掉整棵树", async () => {
    const src = eagerSource(
      [
        { path: "空目录", name: "空目录", isDir: true },
        { path: "报告.md", name: "报告.md", isDir: false },
      ],
      "workspace:workroom-empty-hint",
    );
    renderTree(src, { renderWorkroomLead: () => <div>画像.md</div> });

    expect(await screen.findByText(".agentcore")).toBeTruthy();
    expect(screen.getByText("报告.md")).toBeTruthy();
    fireEvent.click(screen.getByText("空目录"));
    expect(await screen.findByText("空文件夹")).toBeTruthy();
    expect(screen.getByText("报告.md")).toBeTruthy();
    expect(screen.getByText(".agentcore")).toBeTruthy();
  });

  it("虚拟 .agentcore 可被筛选命中", async () => {
    const src = eagerSource(
      [{ path: "报告.md", name: "报告.md", isDir: false }],
      "workspace:workroom-filter-virtual",
    );
    renderTree(src, {
      renderWorkroomLead: () => <div>画像.md</div>,
      filterQuery: ".agentcore",
    });

    expect(await screen.findByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("报告.md")).toBeNull();
  });

  it("虚拟 .agentcore 进全选行表", async () => {
    const src = eagerSource(
      [{ path: "报告.md", name: "报告.md", isDir: false }],
      "workspace:workroom-select-all",
    );
    renderTree(src, { renderWorkroomLead: () => <div>画像.md</div> });

    fireEvent.keyDown(await screen.findByText("报告.md"), {
      key: "a",
      ctrlKey: true,
    });
    expect(screen.getByText("已选择 2 项")).toBeTruthy();
  });

  it("懒加载等 AgentCore/文档 时抽屉转圈，不能当成就绪空层", async () => {
    const hang = deferred<FileNode[]>();
    const src = lazySource({}, "local:workroom-lazy-docs", {
      listDir: async (dir) => {
        if (dir === "AgentCore/文档") return hang.promise;
        if (dir === "") {
          return [
            { path: "AgentCore", name: "AgentCore", isDir: true },
            { path: "报告.md", name: "报告.md", isDir: false },
          ];
        }
        if (dir === "AgentCore") {
          return [{ path: "AgentCore/文档", name: "文档", isDir: true }];
        }
        return [];
      },
    });
    renderTree(src);

    fireEvent.click(await screen.findByText(".agentcore"));
    expect(await screen.findByText("加载中…")).toBeTruthy();
    expect(screen.queryByText("工作稿")).toBeNull();
    expect(screen.queryByText("文档")).toBeNull();
    expect(screen.queryByText("空文件夹")).toBeNull();

    await act(async () => {
      hang.resolve([
        { path: "AgentCore/文档/工作稿", name: "工作稿", isDir: true },
      ]);
    });
    expect(await screen.findByText("工作稿")).toBeTruthy();
    expect(screen.queryByText("加载中…")).toBeNull();
    expect(screen.queryByText("文档")).toBeNull();
  });

  it("展开抽屉时本机 watch 覆盖被摊平的 AgentCore/文档", async () => {
    const watched: string[] = [];
    const src = lazySource(
      {
        "": [
          { path: "AgentCore", name: "AgentCore", isDir: true },
          { path: "报告.md", name: "报告.md", isDir: false },
        ],
        AgentCore: [{ path: "AgentCore/文档", name: "文档", isDir: true }],
        "AgentCore/文档": [
          { path: "AgentCore/文档/工作稿", name: "工作稿", isDir: true },
        ],
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
    expect(await screen.findByText(".agentcore")).toBeTruthy();
    fireEvent.click(screen.getByText(".agentcore"));
    await waitFor(() => {
      expect(watched).toContain("AgentCore");
      expect(watched).toContain("AgentCore/文档");
    });
  });
});
