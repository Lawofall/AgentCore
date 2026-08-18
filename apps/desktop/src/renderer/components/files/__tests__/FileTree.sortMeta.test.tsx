// @vitest-environment jsdom

import { FileTree } from "@/components/files/FileTree";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTreeRowMenu", () => ({
  FileTreeRowMenu: () => null,
}));

const HOUR = 3_600_000;
/** 固定「现在」，让相对日期文案（今天 / 昨天）稳定。 */
const NOW = new Date("2026-08-14T12:00:00").getTime();

function file(
  name: string,
  sizeBytes: number | null,
  mtimeMs: number | null,
): FileNode {
  return { path: name, name, isDir: false, sizeBytes, mtimeMs };
}

function source(entries: FileNode[]): FileSource & { calls: () => number } {
  let calls = 0;
  return {
    id: "workspace:sort",
    label: "工作区",
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
    listDir: async (dir) => {
      if (dir === "") calls++;
      return dir === "" ? entries : [];
    },
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
    calls: () => calls,
  };
}

/** 行文本顺序（树里的 <li>），用来断言兄弟排序。 */
function rowNames(): string[] {
  return screen
    .getAllByRole("button")
    .map((b) => b.querySelector("span")?.textContent ?? "")
    .filter(Boolean);
}

describe("文件树的修改时间", () => {
  it("文件行只显示修改时间；源没给元信息就不占位（不拿 0 B 冒充）", async () => {
    vi.setSystemTime(NOW);
    const src = source([
      file("报告.md", 2048, NOW - HOUR),
      file("无信息.md", null, null),
    ]);
    render(
      <TooltipProvider>
        <FileTree source={src} onOpenFile={() => {}} />
      </TooltipProvider>,
    );

    expect(await screen.findByText("报告.md")).toBeTruthy();
    expect(screen.getByText("11:00")).toBeTruthy();
    expect(screen.queryByText(/KB/)).toBeNull();
    expect(screen.queryByText(/\d+ B\b/)).toBeNull();
    // 缺元信息的那行只有文件名，没有任何「0 B」「未知」占位。
    expect(screen.queryByText(/0 B/)).toBeNull();
    vi.useRealTimers();
  });

  it("按时间排序只重排已加载的层，不重新拉取；缺元信息的沉底", async () => {
    const src = source([
      file("小.md", 10, NOW - 3 * HOUR),
      file("大.md", 5000, NOW - 2 * HOUR),
      file("最新.md", 100, NOW - HOUR),
      file("无信息.md", null, null),
    ]);
    const { rerender } = render(
      <TooltipProvider>
        <FileTree source={src} onOpenFile={() => {}} sortBy="name" />
      </TooltipProvider>,
    );

    await waitFor(() => expect(screen.getByText("大.md")).toBeTruthy());
    const byName = rowNames();
    expect(byName).toEqual([...byName].sort((a, b) => a.localeCompare(b)));
    expect(src.calls()).toBe(1);

    rerender(
      <TooltipProvider>
        <FileTree source={src} onOpenFile={() => {}} sortBy="mtime" />
      </TooltipProvider>,
    );
    await waitFor(() => expect(rowNames()[0]).toBe("最新.md"));
    expect(rowNames()).toEqual(["最新.md", "大.md", "小.md", "无信息.md"]);

    // 换排序是纯重排：一次都没再问过源。
    expect(src.calls()).toBe(1);
  });

  it("目录恒在文件之前，换排序也不例外", async () => {
    const src = source([
      { path: "zzz", name: "zzz", isDir: true, sizeBytes: null, mtimeMs: 1 },
      file("aaa.md", 9999, NOW),
    ]);
    render(
      <TooltipProvider>
        <FileTree source={src} onOpenFile={() => {}} sortBy="mtime" />
      </TooltipProvider>,
    );

    await waitFor(() => expect(screen.getByText("aaa.md")).toBeTruthy());
    expect(rowNames()).toEqual(["zzz", "aaa.md"]);
  });

  it("盘上 AgentCore/（AI 工作间）钉在同级最前，换排序也不例外", async () => {
    const src = source([
      file("报告.md", 100, NOW),
      {
        path: "合同",
        name: "合同",
        isDir: true,
        sizeBytes: null,
        mtimeMs: NOW,
      },
      {
        path: "AgentCore",
        name: "AgentCore",
        isDir: true,
        sizeBytes: null,
        mtimeMs: 1,
      },
    ]);
    render(
      <TooltipProvider>
        <FileTree source={src} onOpenFile={() => {}} sortBy="mtime" />
      </TooltipProvider>,
    );

    expect(await screen.findByText("AI 工作间")).toBeTruthy();
    expect(rowNames()).toEqual(["AI 工作间", "合同", "报告.md"]);
  });
});
