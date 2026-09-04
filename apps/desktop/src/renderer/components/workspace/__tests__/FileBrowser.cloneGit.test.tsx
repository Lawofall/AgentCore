// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { FileBrowser } from "@/components/workspace/FileBrowser";
import type { FileSource } from "@/lib/fileSource";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (sel: (s: { showFile: () => void }) => unknown) =>
    sel({ showFile: vi.fn() }),
}));

vi.mock("@/components/files/FileTree", () => ({
  FileTree: () => <div data-testid="file-tree" />,
}));

const source = {
  id: "workspace:cloud",
  label: "云",
  caps: { watch: false, transfer: true, edit: true, snapshots: true },
} as FileSource;

afterEach(() => {
  cleanup();
});

describe("FileBrowser · 工具条顺序", () => {
  it("左侧先新建再装入：文件 / 文件夹 / 上传 / 克隆", () => {
    render(
      <TooltipProvider>
        <FileBrowser source={source} onCloneGit={vi.fn()} />
      </TooltipProvider>,
    );
    const labels = ["新建文件", "新建文件夹", "上传", "从 Git 克隆"].map(
      (name) => screen.getByRole("button", { name }),
    );
    for (let i = 1; i < labels.length; i++) {
      expect(
        labels[i - 1].compareDocumentPosition(labels[i]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: "刷新" })).toBeNull();
    expect(screen.queryByRole("button", { name: "挂载共享空间" })).toBeNull();
  });
});

describe("FileBrowser · 从 Git 克隆", () => {
  it("onCloneGit 存在时显示并触发", () => {
    const onCloneGit = vi.fn();
    render(
      <TooltipProvider>
        <FileBrowser source={source} onCloneGit={onCloneGit} />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "从 Git 克隆" }));
    expect(onCloneGit).toHaveBeenCalledTimes(1);
  });

  it("未传 onCloneGit 时不显示", () => {
    render(
      <TooltipProvider>
        <FileBrowser source={source} />
      </TooltipProvider>,
    );
    expect(screen.queryByRole("button", { name: "从 Git 克隆" })).toBeNull();
  });
});

describe("FileBrowser · 无挂载共享空间", () => {
  it("工具条不渲染挂载入口或第二根", () => {
    render(
      <TooltipProvider>
        <FileBrowser source={source} />
      </TooltipProvider>,
    );
    expect(screen.queryByRole("button", { name: "挂载共享空间" })).toBeNull();
    expect(screen.queryByTestId("shared-roots")).toBeNull();
  });
});
