import type { FileNode } from "@/lib/fileSource";
import { describe, expect, it } from "vitest";
import {
  isInternalScratchRootName,
  localConvDeskLabel,
  mixLocalRailItems,
  scratchHasUserVisibleFiles,
} from "../localConvScratch";

function node(name: string, over: Partial<FileNode> = {}): FileNode {
  return { path: name, name, isDir: false, ...over };
}

describe("isInternalScratchRootName", () => {
  it("treats AgentCore, .agentcore, and attachments as internal", () => {
    expect(isInternalScratchRootName("AgentCore")).toBe(true);
    expect(isInternalScratchRootName(".agentcore")).toBe(true);
    expect(isInternalScratchRootName("attachments")).toBe(true);
    expect(isInternalScratchRootName("AgentCore/")).toBe(true);
    expect(isInternalScratchRootName("notes.md")).toBe(false);
  });
});

describe("scratchHasUserVisibleFiles", () => {
  it("is true when a user file sits at the root", async () => {
    expect(
      await scratchHasUserVisibleFiles({
        listDir: async () => [node("notes.md")],
      }),
    ).toBe(true);
  });

  it("is false for an empty tree, internals-only, or a missing dir", async () => {
    expect(await scratchHasUserVisibleFiles({ listDir: async () => [] })).toBe(
      false,
    );
    expect(
      await scratchHasUserVisibleFiles({
        listDir: async () => [
          node("AgentCore", { isDir: true }),
          node(".agentcore", { isDir: true }),
          node("attachments", { isDir: true }),
        ],
      }),
    ).toBe(false);
    expect(
      await scratchHasUserVisibleFiles({
        listDir: async () => {
          throw new Error("not found");
        },
      }),
    ).toBe(false);
  });
});

describe("localConvDeskLabel", () => {
  const ws = { wsId: "conv:c1", name: "一次快速对话" };

  it("uses the sidebar conversation title when present", () => {
    expect(localConvDeskLabel(ws, [{ id: "c1", title: "定价讨论" }])).toBe(
      "定价讨论",
    );
  });

  it("falls back to workspace name, then 对话文件 — never 云文件夹 / 未命名对话", () => {
    expect(localConvDeskLabel(ws, [])).toBe("一次快速对话");
    expect(
      localConvDeskLabel({ wsId: "conv:c1", name: "未命名对话" }, []),
    ).toBe("对话文件");
    expect(localConvDeskLabel({ wsId: "conv:c1", name: "云文件夹" }, [])).toBe(
      "对话文件",
    );
    expect(localConvDeskLabel({ wsId: "conv:c1", name: "  " }, [])).toBe(
      "对话文件",
    );
  });
});

describe("mixLocalRailItems", () => {
  it("orders by recent activity", () => {
    const mixed = mixLocalRailItems(
      [
        {
          id: "f1",
          name: "旧夹",
          mode: "local",
          localRootId: "r1",
          localSubpath: null,
        },
      ],
      [
        {
          wsId: "conv:c1",
          name: "新对话文件",
          location: "local",
          rootId: "r1",
          subpath: "conversations/c1",
          hasFiles: true,
        },
      ],
      [
        {
          id: "c1",
          updatedAt: "2026-06-01T00:00:00Z",
          folderId: null,
        },
        {
          id: "other",
          updatedAt: "2020-01-01T00:00:00Z",
          folderId: "f1",
        },
      ],
    );
    expect(
      mixed.map((r) => (r.kind === "conv" ? r.ws.wsId : r.folder.id)),
    ).toEqual(["conv:c1", "f1"]);
  });
});
