import { FileTreeRow } from "@/components/files/FileTreeRow";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
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
    reload: noop,
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

describe("FileTreeRow stage dir badges", () => {
  it("AgentCore/文档/research/debate 显示徽章副文案，普通目录零噪音", () => {
    const { unmount } = renderDir("AgentCore/文档/research", "research", 2);
    expect(screen.getByText("调研约定文档 · 2 件")).toBeTruthy();
    unmount();

    renderDir("AgentCore/文档/debate", "debate", 1);
    expect(screen.getByText("辩论产物 · 1 件")).toBeTruthy();
    // 同屏再渲普通目录不应出徽章
    renderDir("src", "src", 3);
    expect(screen.queryByText(/src ·/)).toBeNull();
    expect(screen.getByText("src")).toBeTruthy();
    expect(screen.queryByText("调研约定文档 · 3 件")).toBeNull();
  });
});

describe("FileTreeRow 约定根呈现名", () => {
  it("盘上 AgentCore/ 显示「.agentcore」，不再用磁盘真名或旧工作间名", () => {
    const { unmount } = renderDir("AgentCore", "AgentCore", 1);
    expect(screen.getByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("AgentCore")).toBeNull();
    expect(screen.queryByText("AI 工作间")).toBeNull();
    unmount();

    // 嵌套的同名目录不是约定根，保持磁盘真名。
    renderDir("src/AgentCore", "AgentCore", 1);
    expect(screen.getByText("AgentCore")).toBeTruthy();
    expect(screen.queryByText(".agentcore")).toBeNull();
  });
});

describe("FileTreeRow load error tone", () => {
  it("展开后加载失败 is muted, not destructive", () => {
    renderDir("docs", "docs", 0, "error");
    const line = screen.getByText("加载失败");
    expect(line.className).toContain("text-muted-foreground");
    expect(line.className).not.toContain("destructive");
  });
});
