import {
  DEFAULT_PROMPT_CATALOG_ID,
  OTHER_FOLDER_ID,
  OTHER_FOLDER_NAME,
  SKILL_GROUP_ORDER,
  buildMineCatalogRows,
  buildPromptCatalog,
  buildPromptRail,
  catalogIdForMemoryTarget,
  flattenPromptCatalog,
  flattenPromptRail,
  mineCatalogId,
  onDemandDropFolder,
  placeholderCatalogId,
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
    { name: "thin_skill", summary: "薄技能", body: "thin-body", group: "" },
  ],
  tools: [],
};

describe("buildPromptCatalog", () => {
  it("出厂按常驻/按需分区：准则身份在常驻，官方怎么做在按需", () => {
    const groups = buildPromptCatalog(base);
    expect(groups.map((g) => g.id)).toEqual(["always", "on_demand"]);
    expect(groups.map((g) => g.label)).toEqual(["常驻", "按需"]);
    expect(groups[0]?.items.map((i) => i.id)).toEqual(["shared", "identity"]);
    expect(groups[1]?.items.map((i) => i.id)).toEqual([
      skillCatalogId("thin_skill"),
    ]);
    const items = flattenPromptCatalog(groups);
    const identity = items.find((i) => i.id === "identity");
    expect(identity?.kind === "identity" && identity.leafIdentity).toBe(
      "<身份>\n叶子\n</身份>",
    );
    expect(DEFAULT_PROMPT_CATALOG_ID).toBe("identity");
    const thin = items.find((i) => i.id === skillCatalogId("thin_skill"));
    expect(thin?.kind === "skill" && thin.label).toBe("薄技能");
    expect(thin?.kind === "skill" && thin.tocGroup).toBe("");
  });

  it("官方怎么做按决策时刻分组排序", () => {
    const groups = buildPromptCatalog({
      ...base,
      skills: [
        { name: "run", summary: "跑命令 / 启服", body: "r", group: "工具" },
        { name: "staffing", summary: "团队拆法", body: "s", group: "编排" },
        {
          name: "local_desk",
          summary: "本机目录进工作区",
          body: "d",
          group: "工作区",
        },
        { name: "ask_kickoff", summary: "开场提问", body: "a", group: "编排" },
      ],
    });
    const skills = flattenPromptCatalog(groups).filter(
      (item) => item.kind === "skill",
    );
    expect(skills.map((row) => row.kind === "skill" && row.skill.name)).toEqual(
      ["staffing", "ask_kickoff", "local_desk", "run"],
    );
    expect(skills.map((row) => row.kind === "skill" && row.tocGroup)).toEqual([
      "编排",
      "编排",
      "工作区",
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
    ).toEqual(["shared", "identity"]);
    const identity = flattenPromptCatalog(groups).find(
      (i) => i.id === "identity",
    );
    expect(identity?.kind === "identity" && identity.leafIdentity).toBe(
      "叶子整段",
    );
  });
});

describe("buildPromptRail", () => {
  it("准则身份在常驻，核在记忆带，官方 HOW 在按需轨", () => {
    const rail = buildPromptRail(base, buildMineCatalogRows([], []), [], null);
    expect(rail.constitution.map((row) => row.id)).toEqual([
      "shared",
      "identity",
    ]);
    expect(rail.memory.map((row) => row.id)).toEqual([
      placeholderCatalogId("preferences"),
      placeholderCatalogId("profile"),
    ]);
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

  it("出厂工具合成一份，按能力面再开场即用排序", () => {
    const rail = buildPromptRail(
      {
        ...base,
        tools: [
          {
            name: "host",
            face: "host_browser",
            resident: false,
            summary: "本机",
            description: "本机",
            parameters: {},
            approval: "grantable",
            available_to: ["worker"],
          },
          {
            name: "file_read",
            face: "file",
            resident: true,
            summary: "读文件",
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
      toolCatalogId("file_read"),
      toolCatalogId("host"),
    ]);
    expect(rail.tools.map((row) => row.tool.resident)).toEqual([true, false]);
    expect(
      flattenPromptRail(rail)
        .filter((row) => row.kind === "tool")
        .map((row) => row.id),
    ).toEqual([toolCatalogId("file_read"), toolCatalogId("host")]);
  });

  it("偏好画像进根，按需自建进其他", () => {
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
    expect(rail.constitution.map((row) => row.id)).toEqual([
      "shared",
      "identity",
    ]);
    expect(rail.memory.map((row) => row.id)).toEqual([
      placeholderCatalogId("preferences"),
      placeholderCatalogId("profile"),
    ]);
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
  it("常驻账号核出现在我的列表，不能当商店货", () => {
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
    expect(rows.map((row) => row.name)).toEqual(["偏好", "画像"]);
    const pref = rows.find((row) => row.name === "偏好");
    expect(pref).toEqual(
      expect.objectContaining({
        id: "pref",
        applyMode: "always",
        aiMaintained: true,
        memoryKind: "preferences",
        listable: false,
      }),
    );
    const profile = rows.find((row) => row.name === "画像");
    expect(profile?.id).toBe("");
    expect(profile?.memoryKind).toBe("profile");
    expect(profile?.listable).toBe(false);
  });

  it("缺核时用占位，按需用户条目可上架", () => {
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
    expect(rows[0]?.memoryKind).toBe("preferences");
    expect(rows[1]?.memoryKind).toBe("profile");
    expect(rows[2]).toEqual(
      expect.objectContaining({
        id: "d1",
        name: "合同审查",
        listable: true,
        applyMode: "on_demand",
      }),
    );
  });

  it("占位目录 id 稳定", () => {
    expect(placeholderCatalogId("preferences")).toBe("placeholder:preferences");
  });

  it("global 偏好/画像 对上我的目录行，文件夹叶子不对", () => {
    const items = flattenPromptRail(
      buildPromptRail(base, buildMineCatalogRows([], []), [], null),
    );
    expect(catalogIdForMemoryTarget("global/preferences", items)).toBe(
      placeholderCatalogId("preferences"),
    );
    expect(catalogIdForMemoryTarget("global/profile", items)).toBe(
      placeholderCatalogId("profile"),
    );
    expect(catalogIdForMemoryTarget("project/F1/profile", items)).toBeNull();
  });

  it("有真实账号核时对上 mine id", () => {
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
    expect(catalogIdForMemoryTarget("global/preferences", items)).toBe(
      mineCatalogId("pref"),
    );
  });
});
