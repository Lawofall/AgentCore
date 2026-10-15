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

  it("不注入虚拟目录，筛选只命中真实文件", () => {
    const childrenOf = withVirtualAgentCore(disk, {});
    const filter = computeFileTreeFilter(childrenOf, ".gagent");
    expect(filter.visible.has("GAgent")).toBe(false);
    expect(filter.visible.has("报告.md")).toBe(false);

    expect(
      flattenVisibleRows({
        childrenOf,
        expanded: new Set(),
      }).map((r) => r.path),
    ).toEqual(["报告.md", "合同"]);
  });

  it("hideRootDirs 只藏指定目录", () => {
    const childrenOf = withVirtualAgentCore(disk, {
      hideRootDirs: ["合同"],
    });
    expect(childrenOf("")?.map((n) => n.path)).toEqual(["报告.md"]);
  });
});

describe("watchDirsForExpanded", () => {
  it("watch 根加上已展开的目录", () => {
    expect(watchDirsForExpanded(new Set(["GAgent"]))).toEqual(["", "GAgent"]);
  });

  it("未展开的目录不进 watch", () => {
    expect(watchDirsForExpanded(new Set(["docs"]))).toEqual(["", "docs"]);
  });
});
