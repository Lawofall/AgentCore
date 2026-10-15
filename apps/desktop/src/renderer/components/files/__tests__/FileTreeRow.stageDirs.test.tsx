import { FileTreeRow } from "@/components/files/FileTreeRow";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTreeRowMenu", () => ({
  FileTreeRowMenu: () => null,
}));

function noop() {}

function renderDir(
  path: string,
  name: string,
  fileCount: number,
  status: "ready" | "error" = "ready",
  reload: () => void = noop,
) {
  const children = Array.from({ length: fileCount }, (_, i) => ({
    path: `${path}/f${i}.md`,
    name: `f${i}.md`,
    isDir: false,
  }));
  const map = new Map<string, FileNode[]>([[path, children]]);
  const source = {
    id: "test",
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
  } as unknown as FileSource;
  const data = {
    childrenOf: (dir: string) =>
      status === "error" ? undefined : map.get(dir),
    statusOf: () => status,
    truncatedOf: () => false,
    ensureDir: noop,
    reload,
    reloadSilent: async () => {},
  };
  const base = {
    depth: 0,
    indentBase: 0,
    source,
    data,
    expanded: status === "error" ? new Set([path]) : new Set<string>(),
    activePath: null,
    creating: null,
    renaming: null,
    dropTarget: null,
    selectedPaths: new Set<string>(),
    dragPaths: [],
    cutPaths: new Set<string>(),
    hasClipboard: false,
    batchMenu: null,
    onToggle: noop,
    onOpenFile: noop,
    onSelect: noop,
    onContextSelect: noop,
    onContextCreate: noop,
    onStartRename: noop,
    onSubmitRename: noop,
    onCancelRename: noop,
    onSubmitCreate: noop,
    onCancelCreate: noop,
    onDelete: noop,
    onCopy: noop,
    onCut: noop,
    onPaste: noop,
    onMoveInto: noop,
    onUpload: noop,
    onDropTarget: noop,
    onReloadDir: noop,
  };
  return render(
    <TooltipProvider>
      <ul>
        <FileTreeRow {...base} node={{ path, name, isDir: true }} />
      </ul>
    </TooltipProvider>,
  );
}

describe("FileTreeRow leftover dirs", () => {
  it("存量 文档/research 当普通目录，无约定柜徽章", () => {
    const { unmount } = renderDir("GAgent/文档/research", "research", 2);
    expect(screen.getByText("research")).toBeTruthy();
    expect(screen.queryByText("调研约定文档 · 2 件")).toBeNull();
    unmount();

    renderDir("GAgent/文档/debate", "debate", 1);
    expect(screen.getByText("debate")).toBeTruthy();
    expect(screen.queryByText("辩论产物 · 1 件")).toBeNull();
    renderDir("src", "src", 3);
    expect(screen.queryByText(/src ·/)).toBeNull();
    expect(screen.getByText("src")).toBeTruthy();
  });
});

describe("FileTreeRow 约定根", () => {
  it("盘上 GAgent/ 用磁盘真名，不盖呈现名", () => {
    const { unmount } = renderDir("GAgent", "GAgent", 1);
    expect(screen.getByText("GAgent")).toBeTruthy();
    expect(screen.queryByText(".gagent")).toBeNull();
    expect(screen.queryByText("AI 工作间")).toBeNull();
    unmount();

    renderDir("src/GAgent", "GAgent", 1);
    expect(screen.getByText("GAgent")).toBeTruthy();
    expect(screen.queryByText(".gagent")).toBeNull();
  });
});

describe("FileTreeRow load error tone", () => {
  it("展开后加载失败 is muted, not destructive", () => {
    renderDir("docs", "docs", 0, "error");
    const line = screen.getByText("加载失败");
    expect(line.className).toContain("text-muted-foreground");
    expect(line.className).not.toContain("destructive");
    expect(
      screen.getByRole("button", { name: "重试" }).className,
    ).not.toContain("destructive");
  });

  it("加载失败 and 重试 call reload so ensureDir-skipped error can retry", () => {
    const reload = vi.fn();
    renderDir("docs", "docs", 0, "error", reload);
    fireEvent.click(screen.getByText("加载失败"));
    expect(reload).toHaveBeenCalledWith("docs");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(reload).toHaveBeenCalledTimes(2);
    expect(reload).toHaveBeenLastCalledWith("docs");
  });
});
