import {
  composeOnDemandSkillContent,
  composeSkillContent,
  skillBodyFromContent,
  skillFileName,
} from "@/services/skillCatalog";
import { describe, expect, it } from "vitest";

describe("skillCatalog helpers", () => {
  it("compose / strip 对得上", () => {
    const content = composeOnDemandSkillContent("审合同时用", "怎么审");
    expect(content).toContain("apply: on_demand");
    expect(content).toContain("description: 审合同时用");
    expect(skillBodyFromContent(content)).toBe("怎么审");
  });

  it("常驻档写 always，保留触发语", () => {
    const content = composeSkillContent("always", "短约束", "要短");
    expect(content).toContain("apply: always");
    expect(content).toContain("description: 短约束");
    expect(skillBodyFromContent(content)).toBe("要短");
  });

  it("无触发语也能组 frontmatter", () => {
    expect(skillBodyFromContent(composeOnDemandSkillContent("", "正文"))).toBe(
      "正文",
    );
  });

  it("文件名缺 .md 就补", () => {
    expect(skillFileName("合同审查")).toBe("合同审查.md");
    expect(skillFileName("合同审查.md")).toBe("合同审查.md");
    expect(skillFileName("")).toBe("未命名提示词.md");
  });
});
