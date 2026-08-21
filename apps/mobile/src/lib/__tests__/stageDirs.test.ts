import { describe, expect, it } from "vitest";
import {
  AGENTCORE_ROOT_LABEL,
  canonicalBrowseDir,
  countDescendantFiles,
  displayDirName,
  flattenWorkroomListing,
  matchesBrowseQuery,
  presentCrumbs,
  presentDirLabel,
  stageDirCaption,
  stageDirMeta,
  stageFileLabel,
  workroomChildren,
} from "../stageDirs";

describe("stageDirs", () => {
  it("根级 research/debate 有元信息，普通目录零噪音", () => {
    expect(stageDirMeta("AgentCore/文档/research")?.label).toBe("调研约定文档");
    expect(stageDirMeta("AgentCore/文档/debate")?.label).toBe("辩论产物");
    expect(stageDirMeta("src")).toBeNull();
    expect(stageDirMeta("AgentCore/文档/research/notes")).toBeNull();
  });

  it("文件路径打约定文档标签；非约定路径无标签", () => {
    expect(stageFileLabel("AgentCore/文档/research/brief.md")).toBe(
      "调研约定文档",
    );
    expect(stageFileLabel("AgentCore/文档/debate/round1.md")).toBe("辩论产物");
    expect(stageFileLabel("src/main.ts")).toBeNull();
  });

  it("副文案含件数", () => {
    const meta = stageDirMeta("AgentCore/文档/debate");
    expect(meta).toBeTruthy();
    if (!meta) return;
    expect(stageDirCaption(meta, 1)).toBe("辩论产物 · 1 件");
  });

  it("统计后代文件数", () => {
    const map = new Map([
      [
        "AgentCore/文档/debate",
        [
          { isDir: false, path: "AgentCore/文档/debate/a.md" },
          { isDir: false, path: "AgentCore/文档/debate/b.md" },
        ],
      ],
    ]);
    expect(
      countDescendantFiles("AgentCore/文档/debate", (d) => map.get(d)),
    ).toBe(2);
  });
});

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
    expect(displayDirName("AgentCore", "AgentCore")).toBe(".agentcore");
    expect(displayDirName("src/AgentCore", "AgentCore")).toBe("AgentCore");
  });
});

describe("presentCrumbs / canonicalBrowseDir", () => {
  it("面包屑认呈现名并跳过 文档/ 壳", () => {
    expect(presentCrumbs("AgentCore")).toEqual([
      { label: ".agentcore", path: "AgentCore" },
    ]);
    expect(presentCrumbs("AgentCore/文档")).toEqual([
      { label: ".agentcore", path: "AgentCore" },
    ]);
    expect(presentCrumbs("AgentCore/文档/research")).toEqual([
      { label: ".agentcore", path: "AgentCore" },
      { label: "research", path: "AgentCore/文档/research" },
    ]);
    expect(canonicalBrowseDir("AgentCore/文档")).toBe("AgentCore");
    expect(canonicalBrowseDir("AgentCore/文档/research")).toBe(
      "AgentCore/文档/research",
    );
    expect(presentDirLabel("AgentCore")).toBe(".agentcore");
  });
});

describe("matchesBrowseQuery", () => {
  it("根上搜 .agentcore 命中约定根，不靠磁盘真名", () => {
    const node = { name: "AgentCore", path: "AgentCore" };
    expect(matchesBrowseQuery(node, ".agentcore")).toBe(true);
    expect(matchesBrowseQuery(node, "AGENTCORE")).toBe(true);
    expect(matchesBrowseQuery({ name: "src", path: "src" }, ".agentcore")).toBe(
      false,
    );
  });
});

describe("workroomChildren", () => {
  it("打开约定根直接是稿夹，钉顶 .agentcore，不露文档壳", () => {
    const tree = new Map([
      [
        "",
        [
          { name: "AAA", path: "AAA", isDir: true },
          { name: "AgentCore", path: "AgentCore", isDir: true },
          { name: "合同", path: "合同", isDir: true },
          { name: "z.md", path: "z.md", isDir: false },
        ],
      ],
      ["AgentCore", [{ name: "文档", path: "AgentCore/文档", isDir: true }]],
      [
        "AgentCore/文档",
        [
          { name: "工作稿", path: "AgentCore/文档/工作稿", isDir: true },
          { name: "research", path: "AgentCore/文档/research", isDir: true },
          {
            name: "已迁入记忆",
            path: "AgentCore/文档/已迁入记忆",
            isDir: true,
          },
        ],
      ],
    ]);
    const root = workroomChildren(tree, "").map((n) => n.name);
    expect(root[0]).toBe("AgentCore");
    expect(root[root.length - 1]).toBe("z.md");
    expect(root).toEqual(expect.arrayContaining(["AAA", "合同"]));
    expect(root).toHaveLength(4);
    const drawer = workroomChildren(tree, "AgentCore").map((n) => n.name);
    expect(drawer).toHaveLength(2);
    expect(drawer).toEqual(expect.arrayContaining(["research", "工作稿"]));
    expect(drawer).not.toContain("文档");
    expect(drawer).not.toContain("已迁入记忆");
    expect(workroomChildren(tree, "AgentCore/文档").map((n) => n.name)).toEqual(
      drawer,
    );
  });
});
