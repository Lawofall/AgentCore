import {
  DEFAULT_PROMPT_CATALOG_ID,
  OTHER_FOLDER_ID,
  OTHER_FOLDER_NAME,
  SKILL_GROUP_ORDER,
  buildMineCatalogRows,
  buildPromptCatalog,
  buildPromptRail,
  flattenPromptCatalog,
  flattenPromptRail,
  mineCatalogId,
  onDemandDropFolder,
  skillCatalogId,
  toolCatalogId,
} from "@/lib/promptCatalog";
import type { Capabilities } from "@/services/capabilities";
import { describe, expect, it } from "vitest";

const base: Capabilities = {
  guidelines: {
    shared_base: "base",
    worker_leaf: "<身份>\n叶子\n</身份>",
    worker_captain: "<身份>\n可再委派\n</身份>",
    ceo_addon: "<身份>\n主 Agent\n</身份>\n\n<按需目录>\n- x：y\n</按需目录>",
    ceo: "full",
  },
  skills: [
    {
      name: "thin_skill",
      summary: "薄技能",
      body: "thin-body",
      group: "",
      blurb: "",
    },
  ],
  tools: [],
};

describe("buildPromptCatalog", () => {
  it("出厂按常驻/按需分区：准则在常驻，官方怎么做在按需", () => {
    const groups = buildPromptCatalog(base);
    expect(groups.map((g) => g.id)).toEqual(["always", "on_demand"]);
    expect(groups.map((g) => g.label)).toEqual(["常驻", "按需"]);
    expect(groups[0]?.items.map((i) => i.id)).toEqual(["shared"]);
    expect(groups[1]?.items.map((i) => i.id)).toEqual([
      skillCatalogId("thin_skill"),
    ]);
    const items = flattenPromptCatalog(groups);
    expect(items.find((i) => i.id === "identity")).toBeUndefined();
    expect(DEFAULT_PROMPT_CATALOG_ID).toBe("shared");
    const thin = items.find((i) => i.id === skillCatalogId("thin_skill"));
    expect(thin?.kind === "skill" && thin.label).toBe("薄技能");
    expect(thin?.kind === "skill" && thin.tocGroup).toBe("");
  });

  it("官方怎么做按决策时刻分组排序", () => {
    const groups = buildPromptCatalog({
      ...base,
      skills: [
        {
          name: "run",
          summary: "跑命令 / 启服",
          body: "r",
          group: "工具",
          blurb: "",
        },
        {
          name: "page_ui",
          summary: "页面观感",
          body: "p",
          group: "交付",
          blurb: "",
        },
        {
          name: "product_help",
          summary: "本产品用法",
          body: "h",
          group: "产品",
          blurb: "",
        },
      ],
    });
    const skills = flattenPromptCatalog(groups).filter(
      (item) => item.kind === "skill",
    );
    expect(skills.map((row) => row.kind === "skill" && row.skill.name)).toEqual(
      ["page_ui", "product_help", "run"],
    );
    expect(skills.map((row) => row.kind === "skill" && row.tocGroup)).toEqual([
      "交付",
      "产品",
      "工具",
    ]);
    expect([...SKILL_GROUP_ORDER]).toEqual([
      "编排",
      "工作区",
      "交付",
      "产品",
      "工具",
    ]);
  });

  it("无官方怎么做时按需区仍在，只是空的", () => {
    const groups = buildPromptCatalog({
      ...base,
      guidelines: {
        ...base.guidelines,
        worker_leaf: "叶子整段",
        worker_captain: "可再委派整段",
      },
      skills: [],
    });
    expect(groups.map((g) => g.id)).toEqual(["always", "on_demand"]);
    expect(groups.find((g) => g.id === "on_demand")?.items).toEqual([]);
    expect(
      groups.find((g) => g.id === "always")?.items.map((i) => i.id),
    ).toEqual(["shared"]);
    expect(
      flattenPromptCatalog(groups).find((i) => i.id === "identity"),
    ).toBeUndefined();
  });

  it("空基座不进常驻", () => {
    const data: Capabilities = {
      ...base,
      guidelines: { ...base.guidelines, shared_base: "  " },
    };
    const groups = buildPromptCatalog(data);
    expect(groups.find((g) => g.id === "always")?.items).toEqual([]);
    const rail = buildPromptRail(data, buildMineCatalogRows([], []), [], null);
    expect(rail.constitution).toEqual([]);
  });
});

describe("buildPromptRail", () => {
  it("准则在常驻，核不进货架，官方 HOW 收在 rail.official", () => {
    const rail = buildPromptRail(base, buildMineCatalogRows([], []), [], null);
    expect(rail.constitution.map((row) => row.id)).toEqual(["shared"]);
    expect(rail.alwaysMine).toEqual([]);
    expect(rail.folders).toEqual([]);
    expect(rail.official.map((row) => row.id)).toEqual([
      skillCatalogId("thin_skill"),
    ]);
    expect(onDemandDropFolder(rail)).toEqual({
      id: OTHER_FOLDER_ID,
      name: OTHER_FOLDER_NAME,
      source: "other",
      documentId: null,
      items: [],
    });
    expect(rail.tools).toEqual([]);
  });

  it("出厂工具一份列表，按能力面再名字排序", () => {
    const rail = buildPromptRail(
      {
        ...base,
        tools: [
          {
            name: "host",
            face: "host_browser",
            resident: true,
            summary: "本机",
            blurb: "看本机屏幕、键鼠和已打开的应用",
            description: "本机",
            parameters: {},
            approval: "grantable",
            available_to: ["worker"],
          },
          {
            name: "mcp_fs_read",
            face: "file",
            resident: true,
            summary: "本机文件",
            blurb: "连接器报出的读文件动作",
            description: "本机文件",
            parameters: {},
            approval: "never",
            available_to: ["ceo", "worker"],
          },
          {
            name: "read",
            face: "file",
            resident: true,
            summary: "读文件",
            blurb: "打开文本、代码或图片，看里面写了什么",
            description: "读文件",
            parameters: {},
            approval: "never",
            available_to: ["ceo", "worker"],
          },
        ],
      },
      buildMineCatalogRows([], []),
      [],
      null,
    );
    expect(rail.tools.map((row) => row.id)).toEqual([
      toolCatalogId("mcp_fs_read"),
      toolCatalogId("read"),
      toolCatalogId("host"),
    ]);
    expect(rail.tools.map((row) => row.tool.resident)).toEqual([
      true,
      true,
      true,
    ]);
    expect(
      flattenPromptRail(rail)
        .filter((row) => row.kind === "tool")
        .map((row) => row.id),
    ).toEqual([
      toolCatalogId("mcp_fs_read"),
      toolCatalogId("read"),
      toolCatalogId("host"),
    ]);
  });

  it("偏好画像不进货架，按需自建进其他", () => {
    const rail = buildPromptRail(
      base,
      buildMineCatalogRows(
        [
          {
            id: "d1",
            name: "合同审查",
            description: "审合同时用",
            content: "HOW",
            version: "v1",
          },
        ],
        [],
      ),
      [],
      null,
    );
    expect(rail.constitution.map((row) => row.id)).toEqual(["shared"]);
    const other = rail.folders.find((folder) => folder.source === "other");
    expect(other?.items.map((row) => row.id)).toEqual([mineCatalogId("d1")]);
    expect(rail.official.map((row) => row.id)).toEqual([
      skillCatalogId("thin_skill"),
    ]);
  });

  it("我的按需条目出现在其他夹", () => {
    const rail = buildPromptRail(
      base,
      buildMineCatalogRows(
        [
          {
            id: "d1",
            name: "合同审查",
            description: "审合同时用",
            content: "HOW",
            version: "v1",
          },
        ],
        [],
      ),
      [],
      null,
    );
    expect(flattenPromptRail(rail).map((row) => row.label)).toContain(
      "合同审查",
    );
  });

  it("常驻用户条目进根，不进夹", () => {
    const rail = buildPromptRail(
      base,
      buildMineCatalogRows(
        [],
        [
          {
            id: "r1",
            name: "短约束.md",
            description: "",
            applyMode: "always",
            aiMaintained: false,
            disputedAt: null,
            alwaysChars: 800,
            parentId: null,
          },
        ],
      ),
      [],
      null,
    );
    expect(rail.alwaysMine.map((row) => row.label)).toContain("短约束");
    expect(
      rail.folders.flatMap((folder) => folder.items.map((row) => row.label)),
    ).not.toContain("短约束");
  });

  it("路径条目单独成区，不进按需夹", () => {
    const rail = buildPromptRail(
      base,
      buildMineCatalogRows(
        [],
        [
          {
            id: "p1",
            name: "组件.md",
            description: "组件用函数",
            applyMode: "paths",
            aiMaintained: false,
            disputedAt: null,
            alwaysChars: null,
            parentId: "folder-1",
          },
        ],
      ),
      [{ id: "folder-1", name: "前端" }],
      null,
    );
    expect(rail.pathMine.map((row) => row.label)).toEqual(["组件"]);
    expect(
      rail.folders.flatMap((folder) => folder.items.map((row) => row.label)),
    ).not.toContain("组件");
    expect(rail.alwaysMine).toEqual([]);
  });

  it("用户按需文件进自己的夹，官方 HOW 不进用户夹", () => {
    const rail = buildPromptRail(
      {
        ...base,
        skills: [
          {
            name: "staffing",
            summary: "团队拆法",
            body: "s",
            group: "编排",
            blurb: "",
          },
        ],
      },
      buildMineCatalogRows(
        [],
        [
          {
            id: "d1",
            name: "合同审查.md",
            description: "",
            applyMode: "on_demand",
            aiMaintained: false,
            disputedAt: null,
            alwaysChars: null,
            parentId: "f-orch",
          },
        ],
      ),
      [{ id: "f-orch", name: "编排" }],
      "rules",
    );
    const orch = rail.folders.find((folder) => folder.name === "编排");
    expect(orch?.source).toBe("user");
    expect(orch?.items.map((row) => row.label)).toEqual(["合同审查"]);
    expect(rail.official.map((row) => row.label)).toEqual(["团队拆法"]);
  });

  it("homes 参数已取消，官方不进用户夹", () => {
    const rail = buildPromptRail(
      {
        ...base,
        skills: [
          {
            name: "staffing",
            summary: "团队拆法",
            body: "s",
            group: "编排",
            blurb: "",
          },
        ],
      },
      buildMineCatalogRows([], []),
      [{ id: "f-mine", name: "我的夹" }],
      "rules",
    );
    const mineFolder = rail.folders.find((folder) => folder.name === "我的夹");
    expect(mineFolder?.source).toBe("user");
    expect(mineFolder?.items).toEqual([]);
    expect(rail.official.map((row) => row.label)).toEqual(["团队拆法"]);
  });
});

describe("buildMineCatalogRows", () => {
  it("账号核不出现在我的列表", () => {
    const rows = buildMineCatalogRows(
      [],
      [
        {
          id: "pref",
          name: "偏好.md",
          description: "",
          applyMode: "always",
          aiMaintained: true,
          disputedAt: null,
          alwaysChars: 1200,
          parentId: null,
        },
      ],
    );
    expect(rows).toEqual([]);
  });

  it("按需用户条目可上架，不补空核", () => {
    const rows = buildMineCatalogRows(
      [
        {
          id: "d1",
          name: "合同审查",
          description: "审合同时用",
          content: "HOW",
          version: "v1",
        },
      ],
      [],
    );
    expect(rows).toEqual([
      expect.objectContaining({
        id: "d1",
        name: "合同审查",
        listable: true,
        applyMode: "on_demand",
      }),
    ]);
  });

  it("存量账号核不进目录", () => {
    const items = flattenPromptRail(
      buildPromptRail(
        base,
        buildMineCatalogRows(
          [],
          [
            {
              id: "pref",
              name: "偏好.md",
              description: "",
              applyMode: "always",
              aiMaintained: true,
              disputedAt: null,
              alwaysChars: 1200,
              parentId: null,
            },
          ],
        ),
        [],
        null,
      ),
    );
    expect(items.some((row) => row.id === mineCatalogId("pref"))).toBe(false);
  });
});
