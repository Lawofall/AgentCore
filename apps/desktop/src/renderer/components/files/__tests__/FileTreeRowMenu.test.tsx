// @vitest-environment jsdom

import { FileTreeRowMenu } from "@/components/files/FileTreeRowMenu";
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
  notifyActionError: vi.fn(),
  notifyWarning: vi.fn(),
  notifyInfo: vi.fn(),
}));

afterEach(() => {
  cleanup();
});

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView ??= () => {};
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
});

function stubSource(
  overrides: Partial<FileSource> & { download?: FileSource["download"] },
): FileSource {
  return {
    id: "workspace:menu",
    label: "工作区",
    caps: { watch: false, transfer: true, edit: true, snapshots: true },
    listDir: async () => [],
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
    ...overrides,
  };
}

function openMenu(node: FileNode, source: FileSource) {
  render(
    <ContextMenu>
      <ContextMenuTrigger>row</ContextMenuTrigger>
      <FileTreeRowMenu
        node={node}
        source={source}
        hasClipboard={false}
        batch={null}
        onContextCreate={vi.fn()}
        onStartRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenFile={vi.fn()}
        onCopy={vi.fn()}
        onCut={vi.fn()}
        onPaste={vi.fn()}
        onReloadDir={vi.fn()}
      />
    </ContextMenu>,
  );
  fireEvent.contextMenu(screen.getByText("row"));
}

describe("FileTreeRowMenu 目录下载", () => {
  it("目录行不出现 zip 下载，可写源仍有新建文件", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    openMenu(
      { path: "docs", name: "docs", isDir: true },
      stubSource({ download }),
    );
    expect(await screen.findByText("新建文件")).toBeTruthy();
    expect(screen.getByText("新建子文件夹")).toBeTruthy();
    expect(screen.queryByText("下载")).toBeNull();
    expect(screen.queryByText("新建文件夹")).toBeNull();
    expect(download).not.toHaveBeenCalled();
  });

  it("只读协作桌（无 edit）目录行既无下载也无新建", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    openMenu(
      { path: "docs", name: "docs", isDir: true },
      stubSource({
        caps: { watch: false, transfer: true, edit: false, snapshots: false },
        download,
      }),
    );
    expect(screen.queryByText("下载")).toBeNull();
    expect(screen.queryByText("新建文件")).toBeNull();
    expect(screen.queryByText("新建子文件夹")).toBeNull();
    expect(screen.queryByText("新建文件夹")).toBeNull();
  });

  it("文件行仍可下载", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    openMenu(
      { path: "a.md", name: "a.md", isDir: false },
      stubSource({ download }),
    );
    expect(await screen.findByText("下载")).toBeTruthy();
    expect(screen.queryByText(/^导出$/)).toBeNull();
    fireEvent.click(screen.getByText("下载"));
    expect(download).toHaveBeenCalledWith("a.md", "a.md", { isDir: false });
  });

  it("无 transfer 时目录行不出现下载", () => {
    openMenu(
      { path: "docs", name: "docs", isDir: true },
      stubSource({
        caps: { watch: true, transfer: false, edit: true, snapshots: false },
      }),
    );
    expect(screen.queryByText("下载")).toBeNull();
  });
});

describe("FileTreeRowMenu 系统集成", () => {
  it("本机源不出现复制路径", async () => {
    openMenu(
      { path: "a.md", name: "a.md", isDir: false },
      stubSource({
        revealInOsFileManager: vi.fn(),
        openShellAtPath: vi.fn(),
        openWithOsDefaultApp: vi.fn(),
      }),
    );
    expect(await screen.findByText("在资源管理器中显示")).toBeTruthy();
    expect(screen.getByText("在终端打开")).toBeTruthy();
    expect(screen.queryByText("复制路径")).toBeNull();
  });
});

describe("FileTreeRowMenu 导出", () => {
  it("Markdown 可写源只出现一行，点开后选正式文书传 Word + official", async () => {
    const exportMarkdown = vi
      .fn()
      .mockResolvedValue({ path: "a.docx", warnings: [] });
    openMenu(
      { path: "a.md", name: "a.md", isDir: false },
      stubSource({ exportMarkdown }),
    );
    expect(await screen.findByText(/^导出$/)).toBeTruthy();
    fireEvent.click(screen.getByText(/^导出$/));
    expect(exportMarkdown).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: /正式文书/ }));
    expect(exportMarkdown).toHaveBeenCalledWith("a.md", "docx", "official");
  });

  it("弹窗切 PDF 再选技术报告", async () => {
    const exportMarkdown = vi
      .fn()
      .mockResolvedValue({ path: "note.pdf", warnings: [] });
    openMenu(
      { path: "note.md", name: "note.md", isDir: false },
      stubSource({ exportMarkdown }),
    );
    fireEvent.click(await screen.findByText(/^导出$/));
    fireEvent.click(await screen.findByRole("button", { name: "PDF" }));
    fireEvent.click(await screen.findByRole("button", { name: /技术报告/ }));
    expect(exportMarkdown).toHaveBeenCalledWith("note.md", "pdf", "standard");
  });

  it("非 Markdown 不出现导出", async () => {
    openMenu(
      { path: "a.txt", name: "a.txt", isDir: false },
      stubSource({ exportMarkdown: vi.fn() }),
    );
    expect(await screen.findByText("打开")).toBeTruthy();
    expect(screen.queryByText(/^导出$/)).toBeNull();
  });

  it("只读源不出现导出", async () => {
    openMenu(
      { path: "a.md", name: "a.md", isDir: false },
      stubSource({
        caps: { watch: false, transfer: true, edit: false, snapshots: false },
        exportMarkdown: vi.fn(),
      }),
    );
    expect(await screen.findByText("打开")).toBeTruthy();
    expect(screen.queryByText(/^导出$/)).toBeNull();
  });
});
