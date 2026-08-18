/**
 * 工具行标题参数：内部标识不进用户面。
 *
 * 回归钉：`toolDetail` 挑不到常规参数时会回落到「第一个字符串参数」，于是撤队员 / 裁决求助
 * （参数只有 run_id）的标题就成了 `撤回队员 r-a3f2e1c8-…`——用户对不上协作图上的角色名。
 */
import {
  toolDetail,
  toolLabel,
  toolPhaseText,
} from "@/components/assistantLabels";
import { describe, expect, it } from "vitest";

describe("toolDetail · 标题参数", () => {
  it("常规定位参数照常上标题", () => {
    expect(toolDetail({ query: "竞品定价" })).toBe("竞品定价");
    expect(toolDetail({ path: "docs/方案.md" })).toBe("docs/方案.md");
  });

  it("`id` / `*_id` 一律不进标题（回落也不捡）", () => {
    expect(toolDetail({ run_id: "r-a3f2e1c8-9b21" })).toBe("");
    expect(toolDetail({ conversation_id: "c-8f31ab02" })).toBe("");
    expect(toolDetail({ interjection_id: "i-77120c9a" })).toBe("");
    expect(toolDetail({ id: "x-1" })).toBe("");
  });

  it("同时有 id 与可读参数时，取可读的那个", () => {
    expect(toolDetail({ run_id: "r-a3f2e1c8-9b21", reason: "方向跑偏" })).toBe(
      "方向跑偏",
    );
  });

  it("wait 的 reason 不进标题（仅记日志，不当作用户正文）", () => {
    expect(
      toolDetail(
        { reason: "工程实践研究员已完成，学术视角研究员仍在跑…" },
        "wait",
      ),
    ).toBe("");
    expect(toolDetail({ reason: "方向跑偏" })).toBe("方向跑偏");
  });
});

describe("toolLabel", () => {
  it("wait 展示名为 Wait（与桌面 TOOL_META 对齐）", () => {
    expect(toolLabel("wait")).toBe("Wait");
  });
});

describe("toolPhaseText · 等待态", () => {
  it("联网 / git 阶段写汉字", () => {
    expect(toolPhaseText("querying")).toBe("正在检索");
    expect(toolPhaseText("queued")).toBe("排队中");
    expect(toolPhaseText("fallback")).toBe("改用备用");
    expect(toolPhaseText("fetching")).toBe("正在打开页面");
    expect(toolPhaseText("git_remote")).toBe("连接远端");
  });

  it("未知阶段回落进行中，空相位不画", () => {
    expect(toolPhaseText("brand_new_phase")).toBe("进行中");
    expect(toolPhaseText(undefined)).toBeNull();
  });
});
