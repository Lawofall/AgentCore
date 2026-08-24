import { describe, expect, it } from "vitest";
import { actAuthorizedByLabel, formatActBandLabel } from "../actAuthLabels";

describe("actAuthorizedByLabel", () => {
  it("maps authorized_by contract values", () => {
    expect(actAuthorizedByLabel("stage_card")).toBe("经推进卡授权");
    expect(actAuthorizedByLabel("auto")).toBe("自动开辩");
    expect(actAuthorizedByLabel("preview")).toBe("已授权开跑");
    expect(actAuthorizedByLabel(null)).toBeNull();
    expect(actAuthorizedByLabel(undefined)).toBeNull();
  });
});

describe("formatActBandLabel", () => {
  it("appends auth badge when present", () => {
    expect(formatActBandLabel("辩论对抗", "act-2", "stage_card")).toBe(
      "辩论对抗 · 经推进卡授权",
    );
    expect(formatActBandLabel(null, "act-2", "auto")).toBe("act-2 · 自动开辩");
    expect(formatActBandLabel("调研", "act-1", null)).toBe("调研");
  });
});
