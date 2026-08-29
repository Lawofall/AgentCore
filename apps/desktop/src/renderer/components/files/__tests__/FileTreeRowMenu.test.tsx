// @vitest-environment jsdom

import { FileTreeRowMenu } from "@/components/files/FileTreeRowMenu";
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
  notifyActionError: vi.fn(),
  notifyWarning: vi.fn(),
  notifyInfo: vi.fn(),
}));

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
  it("caps.transfer 时目录行出现下载，并带 isDir", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    openMenu(
      { path: "docs", name: "docs", isDir: true },
      stubSource({ download }),
    );
    fireEvent.click(await screen.findByText("下载"));
    expect(download).toHaveBeenCalledWith("docs", "docs.zip", { isDir: true });
  });

  it("只读共享空间（无 edit）目录行仍可下载", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    openMenu(
      { path: "shared-dir", name: "shared-dir", isDir: true },
      stubSource({
        caps: { watch: false, transfer: true, edit: false, snapshots: false },
        download,
      }),
    );
    expect(await screen.findByText("下载")).toBeTruthy();
    expect(screen.queryByText("新建文件")).toBeNull();
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
