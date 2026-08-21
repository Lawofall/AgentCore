import { AGENTCORE_ROOT_LABEL, flattenWorkroomListing } from "@/lib/stageDirs";
import { describe, expect, it } from "vitest";

describe("flattenWorkroomListing", () => {
  it("把 文档/ 的子项提升一级，丢掉文档壳和迁移归档", () => {
    const own = [{ name: "文档" }, { name: "leftover.md" }];
    const docs = [
      { name: "工作稿" },
      { name: "research" },
      { name: "已迁入记忆" },
    ];
    expect(flattenWorkroomListing(own, docs).map((n) => n.name)).toEqual([
      "leftover.md",
      "工作稿",
      "research",
    ]);
  });
});

describe("AGENTCORE_ROOT_LABEL", () => {
  it("呈现名是小写点目录 .agentcore", () => {
    expect(AGENTCORE_ROOT_LABEL).toBe(".agentcore");
  });
});
