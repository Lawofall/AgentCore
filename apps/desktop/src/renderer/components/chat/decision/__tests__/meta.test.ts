import { parseCheckpointIntent } from "@/lib/checkpointIntent";
import { Clock, Pencil } from "lucide-react";
import { describe, expect, it } from "vitest";
import { QuestionMark } from "../QuestionMark";
import {
  ASK_INTENT_META,
  SETTLED_UNKNOWN_LABEL,
  askResolvedDisplay,
  askResolvedOutcome,
} from "../meta";

describe("decision meta", () => {
  it("sr-only caption; list bodies keep side-effect CTAs; continue has no slogan", () => {
    expect(ASK_INTENT_META.decision.activeCaption).toBe("需要你拍板");
    expect(ASK_INTENT_META.organize_plan.activeCaption).toBe("需要你拍板");
    expect(ASK_INTENT_META).not.toHaveProperty("daily_review");
    expect(ASK_INTENT_META.decision.cta).toBe("提交");
    expect(ASK_INTENT_META.organize_plan.cta).toBe("确认并整理");
    expect(askResolvedOutcome("decision", "continue").label).toBe("");
    expect(askResolvedOutcome("organize_plan", "continue").label).toBe("");
    expect(
      askResolvedOutcome(parseCheckpointIntent("daily_review"), "continue")
        .label,
    ).toBe("");
    expect(askResolvedOutcome("decision", "research_first").label).toBe(
      "已取消本回合",
    );
    expect(askResolvedOutcome("decision", "research_first").tone).toBe("muted");
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
    expect(askResolvedOutcome("decision", "continue").icon).toBe(QuestionMark);
    expect(askResolvedOutcome("decision", "stop").icon).toBe(QuestionMark);
    expect(askResolvedOutcome("decision", "research_first").icon).toBe(
      QuestionMark,
    );
    expect(askResolvedOutcome("decision", "adjust").icon).toBe(Pencil);
    expect(askResolvedOutcome("decision", "timeout").icon).toBe(Clock);
    expect(askResolvedDisplay("decision", null).icon).toBe(QuestionMark);
  });
});
