import {
  DEFAULT_PROMPT_CATALOG_ID,
  buildPromptCatalog,
  flattenPromptCatalog,
  mineCatalogId,
  packCatalogId,
  skillCatalogId,
  withMineSkills,
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
    { name: "thin_skill", summary: "薄技能", body: "thin-body" },
    { name: "pack_skill", summary: "包内技能", body: "pack-body" },
  ],
  tools: [],
  packs: [
    {
      id: "legal",
      name: "法律能力",
      summary: "法务",
      skills: [{ name: "pack_skill", summary: "包内技能", body: "pack-body" }],
    },
  ],
};

describe("buildPromptCatalog", () => {
  it("常驻两层 + 按需去重包内技能 + 能力包子项", () => {
    const groups = buildPromptCatalog(base);
    expect(groups.map((g) => g.id)).toEqual(["standing", "on_demand", "packs"]);
    const items = flattenPromptCatalog(groups);
    expect(items.map((i) => i.id)).toEqual([
      "shared",
      "identity",
      skillCatalogId("thin_skill"),
      packCatalogId("legal"),
      skillCatalogId("pack_skill"),
    ]);
    const identity = items.find((i) => i.id === "identity");
    expect(identity?.kind === "identity" && identity.leafIdentity).toBe(
      "<身份>\n叶子\n</身份>",
    );
    expect(DEFAULT_PROMPT_CATALOG_ID).toBe("identity");
    const onDemand = groups.find((g) => g.id === "on_demand");
    expect(onDemand?.items.map((i) => i.label)).toEqual(["薄技能"]);
    const packSkill = items.find((i) => i.id === skillCatalogId("pack_skill"));
    expect(packSkill?.kind === "skill" && packSkill.depth).toBe(1);
  });

  it("无包时不造空分组", () => {
    const groups = buildPromptCatalog({
      ...base,
      guidelines: {
        ...base.guidelines,
        worker_leaf: "叶子整段",
        worker_captain: "可再委派整段",
      },
      skills: [],
      packs: [],
    });
    expect(groups.map((g) => g.id)).toEqual(["standing"]);
    expect(flattenPromptCatalog(groups).map((i) => i.id)).toEqual([
      "shared",
      "identity",
    ]);
    const identity = flattenPromptCatalog(groups).find(
      (i) => i.id === "identity",
    );
    expect(identity?.kind === "identity" && identity.leafIdentity).toBe(
      "叶子整段",
    );
  });
});

describe("withMineSkills", () => {
  it("空列表也插入我的技能组，排在常驻后面", () => {
    const groups = withMineSkills(buildPromptCatalog(base), []);
    expect(groups.map((g) => g.id)).toEqual([
      "standing",
      "mine",
      "on_demand",
      "packs",
    ]);
    expect(groups.find((g) => g.id === "mine")?.items).toEqual([]);
  });

  it("我的技能用文件名做目录行", () => {
    const groups = withMineSkills(buildPromptCatalog(base), [
      {
        id: "d1",
        name: "合同审查",
        description: "审合同时用",
        content: "HOW",
        version: "v1",
        occupies: ["product_help"],
      },
    ]);
    const mine = groups.find((g) => g.id === "mine");
    expect(mine?.items).toEqual([
      expect.objectContaining({
        id: mineCatalogId("d1"),
        kind: "mine",
        label: "合同审查",
        occupies: ["product_help"],
      }),
    ]);
  });
});
