import { USER_INTERACTION_KIND_VALUES } from "@agentcore/contract-types";
import { describe, expect, it } from "vitest";
import {
  INTERACTION_CARD_NAME,
  INTERACTION_CARD_NAME_UNKNOWN,
  interactionCardName,
} from "./interactionCardName";

describe("INTERACTION_CARD_NAME", () => {
  it("covers every UserInteractionKind; unknown keys do not inherit 工具审批", () => {
    expect(Object.keys(INTERACTION_CARD_NAME).sort()).toEqual(
      [...USER_INTERACTION_KIND_VALUES].sort(),
    );
    expect(INTERACTION_CARD_NAME).toEqual({
      approval: "工具审批",
      escalation: "拍板请求",
      ask_user: "提问确认",
      plan_review: "计划复核",
      team_preview: "开工确认",
      stage_card: "推进卡",
    });
    expect(interactionCardName("approval")).toBe("工具审批");
    expect(interactionCardName("what_is_this")).toBe(
      INTERACTION_CARD_NAME_UNKNOWN,
    );
    expect(interactionCardName("what_is_this")).not.toBe(
      INTERACTION_CARD_NAME.approval,
    );
    expect(interactionCardName("toString")).toBe(INTERACTION_CARD_NAME_UNKNOWN);
  });
});
