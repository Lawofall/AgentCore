import {
  AGENTCORE_ROOT,
  countDescendantFiles,
  isAgentCoreMemoryDirPath,
  isAgentCoreRootDir,
} from "@/lib/stageDirs";
import { describe, expect, it } from "vitest";

describe("stageDirs", () => {
  it("约定根是盘上目录名 GAgent", () => {
    expect(AGENTCORE_ROOT).toBe("GAgent");
    expect(isAgentCoreRootDir("GAgent")).toBe(true);
    expect(isAgentCoreRootDir("GAgent/文档")).toBe(false);
    expect(isAgentCoreRootDir("src/GAgent")).toBe(false);
  });

  it("hides the leftover AgentCore/记忆 dir path", () => {
    expect(isAgentCoreMemoryDirPath("GAgent/记忆")).toBe(true);
    expect(isAgentCoreMemoryDirPath("GAgent/记忆/画像.md")).toBe(true);
    expect(isAgentCoreMemoryDirPath("GAgent")).toBe(false);
    expect(isAgentCoreMemoryDirPath("记忆")).toBe(false);
    expect(isAgentCoreMemoryDirPath("docs/记忆")).toBe(false);
  });

  it("统计后代文件数（含子目录内文件）", () => {
    const map = new Map<string, { isDir: boolean; path: string }[]>([
      [
        "GAgent",
        [
          { isDir: false, path: "GAgent/a.md" },
          { isDir: true, path: "GAgent/文档" },
        ],
      ],
      ["GAgent/文档", [{ isDir: false, path: "GAgent/文档/b.md" }]],
    ]);
    expect(countDescendantFiles("GAgent", (d) => map.get(d))).toBe(2);
    expect(countDescendantFiles("missing", (d) => map.get(d))).toBe(0);
  });
});
