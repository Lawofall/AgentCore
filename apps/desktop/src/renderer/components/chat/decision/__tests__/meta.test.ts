import { describe, expect, it } from "vitest";
import {
  ASK_INTENT_META,
  SETTLED_UNKNOWN_LABEL,
  askResolvedDisplay,
  askResolvedOutcome,
} from "../meta";

describe("decision meta", () => {
  it("ask kickoff / proposal_pick / risk_ack / organize_plan / daily_review keep settled labels", () => {
    expect(askResolvedOutcome("kickoff", "continue").label).toBe("");
    expect(askResolvedOutcome("decision", "continue").label).toBe("");
    expect(askResolvedOutcome("proposal_pick", "continue").label).toBe(
      "已选定方案",
    );
    expect(askResolvedOutcome("risk_ack", "continue").label).toBe(
      "已确认风险处理项",
    );
    expect(askResolvedOutcome("organize_plan", "continue").label).toBe(
      "已确认整理方案",
    );
    expect(askResolvedOutcome("daily_review", "continue").label).toBe(
      "已确认复盘提案",
    );
    expect(ASK_INTENT_META.daily_review.cta).toBe("确认落盘");
    expect(ASK_INTENT_META.daily_review.activeCaption).toBe(
      "复盘提案 · 确认要落盘的项",
    );
    expect(askResolvedOutcome("kickoff", "research_first").label).toBe(
      "已取消本回合",
    );
    expect(askResolvedOutcome("kickoff", "research_first").tone).toBe("muted");
    expect(askResolvedOutcome("decision", "stop").label).toBe("已取消本回合");
    expect(askResolvedOutcome("decision", "stop").tone).toBe("muted");
    expect(askResolvedDisplay("decision", "stop").label).toBe("已取消本回合");
    expect(askResolvedDisplay("decision", "timeout").label).toBe(
      "未及时回应，已自行收尾",
    );
    expect(askResolvedDisplay("decision", null).label).toBe(
      SETTLED_UNKNOWN_LABEL,
    );
    expect(askResolvedDisplay("decision", undefined).label).toBe(
      SETTLED_UNKNOWN_LABEL,
    );
  });

  it("ask resume captions share one table", () => {
    expect(ASK_INTENT_META.kickoff.activeCaption).toBe("需要你拍板");
    expect(ASK_INTENT_META.kickoff.cta).toBe("提交");
    expect(ASK_INTENT_META.decision.activeCaption).toBe(
      ASK_INTENT_META.kickoff.activeCaption,
    );
  });
});
