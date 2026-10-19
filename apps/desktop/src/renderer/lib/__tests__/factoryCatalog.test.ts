import {
  officialSkillNames,
  omittedFactoryAfterToggle,
} from "@/lib/factoryCatalog";
import { skillCatalogId } from "@/lib/promptCatalog";
import { describe, expect, it } from "vitest";

describe("factoryCatalog", () => {
  it("flips one official HOW on the omit list", () => {
    expect(omittedFactoryAfterToggle([], "page_ui", false)).toEqual([
      "page_ui",
    ]);
    expect(omittedFactoryAfterToggle(["page_ui"], "page_ui", true)).toEqual([]);
    expect(omittedFactoryAfterToggle(["page_ui"], "prompts", false)).toEqual([
      "page_ui",
      "prompts",
    ]);
  });

  it("reads consult names from official cards", () => {
    expect(
      officialSkillNames([
        {
          id: skillCatalogId("page_ui"),
          kind: "skill",
          group: "factory",
          label: "page_ui",
          depth: 0,
          tocGroup: "交付",
          parentId: null,
          skill: {
            name: "page_ui",
            summary: "when",
            body: "how",
            group: "交付",
            blurb: "页面",
          },
        },
      ]),
    ).toEqual(["page_ui"]);
  });
});
