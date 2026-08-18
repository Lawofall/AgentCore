import {
  debriefFromHandoffArgs,
  hasDebriefDetails,
  hasSuccessfulHandoff,
  isSuccessfulHandoff,
} from "@/components/handoffBrief";
import { describe, expect, it } from "vitest";

describe("handoffBrief 判定", () => {
  it("只有 toolName=handoff 且 status=success 算成功", () => {
    expect(isSuccessfulHandoff("handoff", "success")).toBe(true);
    expect(isSuccessfulHandoff("handoff", "error")).toBe(false);
    expect(isSuccessfulHandoff("handoff", "running")).toBe(false);
    expect(isSuccessfulHandoff("web_search", "success")).toBe(false);
  });

  it("toolCalls 里已有成功 handoff ↔ 页脚该跳过", () => {
    expect(
      hasSuccessfulHandoff([
        { toolName: "web_search", status: "success" },
        { toolName: "handoff", status: "success" },
      ]),
    ).toBe(true);
    expect(
      hasSuccessfulHandoff([{ toolName: "handoff", status: "error" }]),
    ).toBe(false);
    expect(hasSuccessfulHandoff([])).toBe(false);
  });

  it("从参数抽出简报字段，不搬 motion_card", () => {
    const debrief = debriefFromHandoffArgs({
      summary: "交叉验证完成",
      key_points: ["要点一", ""],
      assumptions: "公开报道为准",
      next_steps: "建议开辩",
      motion_card: { motion: "该不该开辩", form: "debate" },
    });
    expect(debrief.summary).toBe("交叉验证完成");
    expect(debrief.key_points).toEqual(["要点一"]);
    expect(debrief.assumptions).toBe("公开报道为准");
    expect(debrief.next_steps).toBe("建议开辩");
    expect(debrief.motion_card).toBeUndefined();
  });

  it("hasDebriefDetails 只认要点/假设/下一步", () => {
    expect(hasDebriefDetails({ summary: "只写了结论" })).toBe(false);
    expect(hasDebriefDetails({ summary: "有", key_points: ["a"] })).toBe(true);
  });
});
