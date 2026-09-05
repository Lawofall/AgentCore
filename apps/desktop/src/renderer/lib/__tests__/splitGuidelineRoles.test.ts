import {
  extractCeoIdentity,
  splitWorkerGuideline,
} from "@/lib/splitGuidelineRoles";
import { describe, expect, it } from "vitest";

const CONTRACT =
  "【落盘文件】（form=files）成品写入工作区；正文只报路径、怎么用、关键取舍。";

const LEAF = `<身份>
叶子身份。
</身份>

${CONTRACT}`;

describe("splitWorkerGuideline", () => {
  it("keeps only the tagged identity and drops a trailing contract", () => {
    expect(splitWorkerGuideline(LEAF)).toBe("<身份>\n叶子身份。\n</身份>");
    expect(splitWorkerGuideline(LEAF)).not.toContain("落盘文件");
  });

  it("treats untagged catalog text as identity-only", () => {
    expect(splitWorkerGuideline("叶子身份正文")).toBe("叶子身份正文");
  });

  it("returns empty for blank input", () => {
    expect(splitWorkerGuideline("  ")).toBe("");
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
