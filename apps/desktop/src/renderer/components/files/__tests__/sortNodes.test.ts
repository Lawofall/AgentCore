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

  it("盘上 GAgent/ 钉在同级最前", () => {
    const nodes = [dir("GAgent"), file("报告.md"), dir("合同")];
    expect(sortNodes(nodes).map((n) => n.path)).toEqual([
      "GAgent",
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
      dir("src/GAgent"),
      file("src/z.ts"),
      dir("GAgent"),
      file("readme.md"),
    ]);
    expect(map.get("")?.map((n) => n.path)).toEqual([
      "GAgent",
      "src",
      "readme.md",
    ]);
    expect(map.get("src")?.map((n) => n.path)).toEqual([
      "src/GAgent",
      "src/z.ts",
    ]);
  });
});
