import { bucketTree, sortNodes } from "@/components/files/useFileTreeData";
import type { FileNode } from "@/lib/fileSource";
import { describe, expect, it } from "vitest";

const dir = (path: string): FileNode => ({
  path,
  name: path.slice(path.lastIndexOf("/") + 1),
  isDir: true,
});
const file = (path: string): FileNode => ({
  path,
  name: path.slice(path.lastIndexOf("/") + 1),
  isDir: false,
});

describe("sortNodes", () => {
  it("目录在前、文件在后，各自按名排", () => {
    const nodes = [file("b.md"), dir("zzz"), file("a.md"), dir("src")];
    expect(sortNodes(nodes).map((n) => n.path)).toEqual([
      "src",
      "zzz",
      "a.md",
      "b.md",
    ]);
  });

  it("盘上 AgentCore/（.agentcore）钉在同级最前", () => {
    const nodes = [dir("AgentCore"), file("报告.md"), dir("合同")];
    expect(sortNodes(nodes).map((n) => n.path)).toEqual([
      "AgentCore",
      "合同",
      "报告.md",
    ]);
  });

  it("按修改时间降序，缺 mtime 沉底后再按名", () => {
    const older = { ...file("older.md"), mtimeMs: 1 };
    const newer = { ...file("newer.md"), mtimeMs: 9 };
    const missing = { ...file("zzz.md"), mtimeMs: null };
    expect(
      sortNodes([older, missing, newer], "mtime").map((n) => n.path),
    ).toEqual(["newer.md", "older.md", "zzz.md"]);
  });

  it("嵌套的同名目录仍按普通目录排", () => {
    const map = bucketTree([
      dir("src"),
      dir("src/AgentCore"),
      file("src/z.ts"),
      dir("AgentCore"),
      file("readme.md"),
    ]);
    expect(map.get("")?.map((n) => n.path)).toEqual([
      "AgentCore",
      "src",
      "readme.md",
    ]);
    expect(map.get("src")?.map((n) => n.path)).toEqual([
      "src/AgentCore",
      "src/z.ts",
    ]);
  });
});
