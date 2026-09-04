import {
  extractCeoIdentity,
  splitWorkerGuideline,
  workerContractFromGuidelines,
} from "@/lib/splitGuidelineRoles";
import { describe, expect, it } from "vitest";

const CONTRACT =
  "你的交付形态是【落盘文件】。\n\n<写工具谨慎>\n谨慎写盘。\n</写工具谨慎>";

const LEAF = `<身份>
叶子身份。
</身份>

${CONTRACT}`;

const NESTED = `<身份>
可再委派身份。
</身份>

${CONTRACT}`;

describe("splitWorkerGuideline", () => {
  it("splits a tagged identity from the trailing contract", () => {
    expect(splitWorkerGuideline(LEAF)).toEqual({
      identity: "<身份>\n叶子身份。\n</身份>",
      contract: CONTRACT,
    });
  });

  it("treats untagged catalog text as identity-only", () => {
    expect(splitWorkerGuideline("叶子身份正文")).toEqual({
      identity: "叶子身份正文",
      contract: "",
    });
  });

  it("returns empty pieces for blank input", () => {
    expect(splitWorkerGuideline("  ")).toEqual({ identity: "", contract: "" });
  });
});

describe("extractCeoIdentity", () => {
  it("keeps only the identity block and drops the on-demand directory", () => {
    const addon = `<身份>
主 Agent 核。
</身份>

<按需目录>
- team_orchestration_advanced：团队拆法
- lead_subteam：子队拆法
</按需目录>`;
    expect(extractCeoIdentity(addon)).toBe("<身份>\n主 Agent 核。\n</身份>");
    expect(extractCeoIdentity(addon)).not.toContain("按需目录");
    expect(extractCeoIdentity(addon)).not.toContain("lead_subteam");
  });

  it("strips an untagged 按需目录 when identity tags are missing", () => {
    const addon = "路由核心。\n\n<按需目录>\n- run：跑命令\n</按需目录>";
    expect(extractCeoIdentity(addon)).toBe("路由核心。");
  });
});

describe("workerContractFromGuidelines", () => {
  it("reads the shared contract from the leaf template", () => {
    expect(workerContractFromGuidelines(LEAF, NESTED)).toBe(CONTRACT);
  });

  it("falls back to the nested template when the leaf has no contract", () => {
    expect(
      workerContractFromGuidelines("<身份>\n叶子。\n</身份>", NESTED),
    ).toBe(CONTRACT);
  });
});
