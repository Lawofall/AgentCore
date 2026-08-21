import { computeFileTreeFilter } from "@/components/files/fileTreeFilter";
import { flattenVisibleRows } from "@/components/files/fileTreeSelection";
import {
  watchDirsForExpanded,
  withVirtualAgentCore,
} from "@/components/files/fileTreeWorkroom";
import type { FileNode } from "@/lib/fileSource";
import { describe, expect, it } from "vitest";

describe("withVirtualAgentCore", () => {
  const diskRoot: FileNode[] = [
    { path: "报告.md", name: "报告.md", isDir: false },
    { path: "合同", name: "合同", isDir: true },
  ];
  const disk = (dir: string) => (dir === "" ? diskRoot : undefined);

  it("注入的虚拟节点进筛选与 flatten 行表（与渲染同一份 children）", () => {
    const childrenOf = withVirtualAgentCore(disk, { injectVirtual: true });
    const filter = computeFileTreeFilter(childrenOf, ".agentcore");
    expect(filter.visible.has("AgentCore")).toBe(true);
    expect(filter.visible.has("报告.md")).toBe(false);

    expect(
      flattenVisibleRows({
        childrenOf,
        expanded: new Set(),
      }).map((r) => r.path),
    ).toEqual(["AgentCore", "合同", "报告.md"]);
  });

  it("hideRootDirs 之后仍注入虚拟节点", () => {
    const childrenOf = withVirtualAgentCore(disk, {
      injectVirtual: true,
      hideRootDirs: ["合同"],
    });
    expect(childrenOf("")?.map((n) => n.path)).toEqual([
      "AgentCore",
      "报告.md",
    ]);
  });

  it("盘上已有 AgentCore 时不重复注入", () => {
    const withDisk = (dir: string) =>
      dir === ""
        ? [
            { path: "AgentCore", name: "AgentCore", isDir: true },
            { path: "报告.md", name: "报告.md", isDir: false },
          ]
        : undefined;
    const childrenOf = withVirtualAgentCore(withDisk, { injectVirtual: true });
    expect(childrenOf("")?.map((n) => n.path)).toEqual([
      "AgentCore",
      "报告.md",
    ]);
  });
});

describe("watchDirsForExpanded", () => {
  it("展开 .agentcore 时覆盖被摊平的 AgentCore/文档", () => {
    expect(watchDirsForExpanded(new Set(["AgentCore"]))).toEqual([
      "",
      "AgentCore",
      "AgentCore/文档",
    ]);
  });

  it("未展开约定根时不加 文档", () => {
    expect(watchDirsForExpanded(new Set(["docs"]))).toEqual(["", "docs"]);
  });
});
