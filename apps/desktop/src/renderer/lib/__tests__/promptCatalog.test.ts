import {
  DEFAULT_PROMPT_CATALOG_ID,
  buildPromptCatalog,
  flattenPromptCatalog,
  packCatalogId,
  skillCatalogId,
} from "@/lib/promptCatalog";
import type { Capabilities } from "@/services/capabilities";
import { describe, expect, it } from "vitest";

const base: Capabilities = {
  guidelines: {
    shared_base: "base",
    worker_leaf: "<身份>\n叶子\n</身份>\n\n合同正文",
    worker_captain: "<身份>\n可再委派\n</身份>\n\n合同正文",
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
  it("常驻三层 + 按需去重包内技能 + 能力包子项", () => {
    const groups = buildPromptCatalog(base);
    expect(groups.map((g) => g.id)).toEqual(["standing", "on_demand", "packs"]);
    const items = flattenPromptCatalog(groups);
    expect(items.map((i) => i.id)).toEqual([
      "shared",
      "identity",
      "contract",
      skillCatalogId("thin_skill"),
      packCatalogId("legal"),
      skillCatalogId("pack_skill"),
    ]);
    expect(DEFAULT_PROMPT_CATALOG_ID).toBe("identity");
    const onDemand = groups.find((g) => g.id === "on_demand");
    expect(onDemand?.items.map((i) => i.label)).toEqual(["薄技能"]);
    const packSkill = items.find((i) => i.id === skillCatalogId("pack_skill"));
    expect(packSkill?.kind === "skill" && packSkill.depth).toBe(1);
  });

  it("无合同、无包时不造空分组", () => {
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
  });
});
