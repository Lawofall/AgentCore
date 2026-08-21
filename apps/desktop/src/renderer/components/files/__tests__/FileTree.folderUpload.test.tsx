// @vitest-environment jsdom

import { FileTree, type FileTreeHandle } from "@/components/files/FileTree";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { notifySuccess, notifyWarning } from "@/lib/toast";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTreeRowMenu", () => ({
  FileTreeRowMenu: () => null,
}));
vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
  notifyActionError: vi.fn(),
  notifyWarning: vi.fn(),
  notifyInfo: vi.fn(),
}));
vi.mock("@/hooks/useFolders", () => ({ getFolders: vi.fn(() => []) }));

type Tree = { [name: string]: Tree | number };

function fileEntry(name: string, size: number): FileSystemEntry {
  return {
    name,
    isDirectory: false,
    isFile: true,
    file: (ok: (f: File) => void) => ok(new File([new Uint8Array(size)], name)),
  } as unknown as FileSystemEntry;
}

function dirEntry(name: string, tree: Tree): FileSystemEntry {
  const children = Object.entries(tree).map(([child, value]) =>
    typeof value === "number"
      ? fileEntry(child, value)
      : dirEntry(child, value),
  );
  return {
    name,
    isDirectory: true,
    isFile: false,
    createReader: () => {
      let served = false;
      return {
        readEntries: (ok: (batch: FileSystemEntry[]) => void) => {
          ok(served ? [] : children);
          served = true;
        },
      };
    },
  } as unknown as FileSystemEntry;
}

/** 拖入一个目录时浏览器给出的 dataTransfer（`files` 是空的，全靠 entry）。 */
function folderDrop(entry: FileSystemEntry) {
  return {
    types: ["Files"],
    getData: () => "",
    files: [],
    items: [
      { kind: "file", webkitGetAsEntry: () => entry, getAsFile: () => null },
    ],
  };
}

function makeSource(): FileSource & { wrote: string[]; dirs: string[] } {
  const wrote: string[] = [];
  const dirs: string[] = [];
  return {
    id: "workspace:folder:design",
    label: "设计",
    caps: { watch: false, transfer: true, edit: true, snapshots: true },
    listDir: async () => [] as FileNode[],
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async (path) => {
      dirs.push(path);
    },
    move: async () => {},
    delete: async () => {},
    writeBytes: async (path) => {
      wrote.push(path);
    },
    wrote,
    dirs,
  };
}

function renderTree(source: FileSource, ref?: React.Ref<FileTreeHandle>) {
  return render(
    <TooltipProvider>
      <FileTree ref={ref} source={source} onOpenFile={vi.fn()} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("整个文件夹上传到当前工作区", () => {
  it("拖入一个文件夹：层级照搬，空目录也建出来", async () => {
    const source = makeSource();
    const { container } = renderTree(source);
    await screen.findByText("暂无文件");

    fireEvent.drop(container.firstChild as Element, {
      dataTransfer: folderDrop(
        dirEntry("设计", { "a.svg": 4, 图标: { "b.svg": 4 }, 空的: {} }),
      ),
    });

    await waitFor(() =>
      expect(source.wrote.sort()).toEqual(["设计/a.svg", "设计/图标/b.svg"]),
    );
    expect(source.dirs).toContain("设计/空的");
    expect(notifySuccess).toHaveBeenCalledWith("已上传 2 个文件");
  });

  it("忽略项不静默：概述报数，「查看详情」列出逐项清单", async () => {
    const source = makeSource();
    const { container } = renderTree(source);
    await screen.findByText("暂无文件");

    fireEvent.drop(container.firstChild as Element, {
      dataTransfer: folderDrop(
        dirEntry("p", { "a.ts": 4, node_modules: { "x.js": 4 }, "i.db": 4 }),
      ),
    });

    await waitFor(() => expect(notifyWarning).toHaveBeenCalled());
    expect(source.wrote).toEqual(["p/a.ts"]);
    const [message, opts] = vi.mocked(notifyWarning).mock.calls[0];
    expect(message).toBe("已上传 1 个文件");
    expect(opts?.description).toContain("跳过 2 个忽略项");
    expect(notifySuccess).not.toHaveBeenCalled();

    // 概述里的数字不能替代逐项清单。
    act(() => opts?.action?.onClick());
    expect(await screen.findByText("p/node_modules")).toBeTruthy();
    expect(screen.getByText("p/i.db")).toBeTruthy();
  });

  it("工具栏与 ref 都能打开目录选择器（input 带 webkitdirectory）", async () => {
    const ref = createRef<FileTreeHandle>();
    const { container } = renderTree(makeSource(), ref);
    await screen.findByText("暂无文件");

    const folderInput = container.querySelector<HTMLInputElement>(
      "input[webkitdirectory]",
    );
    expect(folderInput).toBeTruthy();

    const click = vi.spyOn(folderInput as HTMLInputElement, "click");
    fireEvent.click(screen.getByRole("button", { name: "上传" }));
    fireEvent.click(screen.getByRole("button", { name: "上传文件夹" }));
    expect(click).toHaveBeenCalledTimes(1);

    ref.current?.triggerUploadFolder();
    expect(click).toHaveBeenCalledTimes(2);
  });

  it("单文件上传的 input 不带 webkitdirectory（两个入口各管各的）", async () => {
    const { container } = renderTree(makeSource());
    await screen.findByText("暂无文件");
    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);
    expect(inputs[0].hasAttribute("webkitdirectory")).toBe(false);
    expect(inputs[1].hasAttribute("webkitdirectory")).toBe(true);
  });
});
