import { describe, expect, it } from "vitest";
import {
  ASK_INTENT_META,
  SETTLED_UNKNOWN_LABEL,
  TEAM_PRIMITIVE_META,
  askResolvedDisplay,
  askResolvedOutcome,
  fillTeamRevisionTemplate,
  teamCorrectionSuffix,
  teamPreviewLead,
  teamPreviewRevisionVersionLabel,
  teamPreviewSettledLead,
  teamResolvedDisplay,
  teamResolvedOutcome,
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

  it("team debate research_first + continue-with-note overrides", () => {
    expect(teamResolvedOutcome("debate", "research_first", false).label).toBe(
      "已选先调研 · 辩论未开赛",
    );
    expect(teamResolvedOutcome("delegate", "research_first", false).label).toBe(
      "已取消 · 团队未启动",
    );
    expect(teamResolvedOutcome("delegate", "timeout", false).label).toBe(
      "未及时回应，团队未启动",
    );
    expect(teamResolvedOutcome("debate", "timeout", false).label).toBe(
      "未及时回应，辩论未开赛",
    );
    expect(teamResolvedDisplay("delegate", null, false).label).toBe(
      SETTLED_UNKNOWN_LABEL,
    );
    expect(teamResolvedDisplay("debate", undefined, false).label).toBe(
      SETTLED_UNKNOWN_LABEL,
    );
    expect(teamResolvedDisplay("delegate", "timeout", false).label).toBe(
      "未及时回应，团队未启动",
    );
    expect(teamResolvedOutcome("delegate", "continue", true).label).toBe(
      "已授权开工 · 嘱咐已注入队员",
    );
    expect(teamResolvedOutcome("debate", "continue", true).label).toBe(
      "已授权开赛 · 嘱咐已注入",
    );
    expect(teamResolvedOutcome("delegate", "adjust", true).label).toBe(
      "已调整 · 已交回修订",
    );
    expect(teamResolvedOutcome("debate", "adjust", true).label).toBe(
      "已调整 · 已交回修订",
    );
  });

  it("teamCorrectionSuffix 对账排除/收紧；缺省空同旧", () => {
    expect(teamCorrectionSuffix({})).toBe("");
    expect(teamCorrectionSuffix({ excluded_run_ids: [] })).toBe("");
    expect(
      teamCorrectionSuffix({
        excluded_run_ids: ["r1"],
        write_capability_overrides: [],
      }),
    ).toBe(" · 已排除 1 岗");
    expect(
      teamCorrectionSuffix({
        excluded_run_ids: ["r1", "r2"],
        write_capability_overrides: [{ run_id: "r3", capability: "text_only" }],
      }),
    ).toBe(" · 已排除 2 岗 · 已收紧写盘");
  });

  it("ask resume captions share one table", () => {
    expect(ASK_INTENT_META.kickoff.activeCaption).toBe("需要你拍板");
    expect(ASK_INTENT_META.kickoff.cta).toBe("提交");
    expect(ASK_INTENT_META.decision.activeCaption).toBe(
      ASK_INTENT_META.kickoff.activeCaption,
    );
    expect(TEAM_PRIMITIVE_META.debate.resumeCta).toBe("授权开赛");
    expect(TEAM_PRIMITIVE_META.delegate.adjustCta).toBe("交回修订");
    expect(teamPreviewRevisionVersionLabel("delegate", 1)).toBeNull();
    expect(teamPreviewRevisionVersionLabel("delegate", undefined)).toBeNull();
    expect(teamPreviewRevisionVersionLabel("debate", 2)).toBe("第 2 版");
    expect(
      fillTeamRevisionTemplate(TEAM_PRIMITIVE_META.delegate.revision.added, {
        name: "撰写员",
      }),
    ).toBe("新增 撰写员");
    expect(TEAM_PRIMITIVE_META.delegate.revision.caption).toBe(
      "按你的意见修订",
    );
    expect(TEAM_PRIMITIVE_META.debate.revision.noteLabel).toBe("你交回的意见");
  });

  it("teamPreviewLead prefers wire headline; falls back to headcount", () => {
    expect(
      teamPreviewLead({
        primitive: "delegate",
        headline: "MVP主流程 · 预计 3 人",
        workerCount: 3,
        sideCount: 0,
      }),
    ).toBe("MVP主流程 · 预计 3 人");
    expect(
      teamPreviewLead({
        primitive: "delegate",
        headline: "",
        workerCount: 2,
        sideCount: 0,
      }),
    ).toBe("预计 2 人开工");
    expect(
      teamPreviewSettledLead({
        primitive: "delegate",
        headline: "",
        workerCount: 2,
        sideCount: 0,
      }),
    ).toBe("2 人");
    expect(
      teamPreviewSettledLead({
        primitive: "debate",
        headline: null,
        workerCount: 0,
        sideCount: 2,
      }),
    ).toBe("2 方");
    expect(
      teamPreviewLead({
        primitive: "debate",
        headline: null,
        workerCount: 0,
        sideCount: 2,
      }),
    ).toBe("预计 2 方开赛");
  });
});
