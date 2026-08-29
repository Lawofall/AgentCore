// @vitest-environment jsdom
/**
 * 终审区钻取惯例（全场统一「名字/身份行 = 打开 run 详情侧栏」）：
 * - 「主持人终审」标题 + 模型徽章这组身份行在 moderatorRun 在时是钻取按钮，
 *   侧栏标题沿用「主持人」；
 * - 「裁决过程」文字链接已删（文字链接只留给就地展开）；
 * - moderatorRun 缺席（进行中 / 旧产物）时标题退回纯文本。
 *
 * 三区布局（BLUF）：裁决卡 → 战果对照 → 留给你的。
 */

import type { Execution, RunNode } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DebateModel } from "../../model";
import { FinaleStage } from "../FinaleStage";

function moderatorRun(id = "moderator"): RunNode {
  return {
    id,
    agentId: id,
    status: "completed",
    kind: "agent",
    model: "deepseek/deepseek-v4-flash",
    parentRunId: null,
    continuesRunId: null,
    receivedContext: [],
  } as unknown as RunNode;
}

function makeModel(overrides: Partial<DebateModel> = {}): DebateModel {
  return {
    form: "debate",
    motion: "是否采用方案 A",
    stopReason: null,
    moderatorRunId: "moderator",
    narrativeFirst: false,
    rounds: [],
    brief: null,
    sides: null,
    closings: [],
    opening: null,
    settled: true,
    ...overrides,
  } as DebateModel;
}

function settledBriefModel(overrides: Partial<DebateModel> = {}): DebateModel {
  return makeModel({
    brief: {
      leaning: "倾向正方",
      confidence: "high",
      decisive: "证据更扎实",
      crux: "成本可否接受",
      recommendation: "先做试点",
      strongest_points: { pro: "ROI 清晰", con: "风险未清" },
      handoffs: [
        { kind: "value", text: "要不要牺牲速度" },
        { kind: "fact", text: "实际成本" },
        { kind: "question", text: "试点范围" },
      ],
    },
    sides: [
      {
        key: "pro",
        name: "正方",
        stance: "pro",
        model: undefined,
        is_subject: false,
      },
      {
        key: "con",
        name: "反方",
        stance: "con",
        model: undefined,
        is_subject: false,
      },
    ],
    ...overrides,
  });
}

function executionWith(runs: RunNode[]): Execution {
  return {
    status: "completed",
    runs,
    agents: [],
    frames: [],
    debate: null,
    debateRounds: [],
  } as unknown as Execution;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FinaleStage 钻取惯例", () => {
  it("身份行（标题 + 模型徽章）是钻取按钮，裁决过程链接已删", () => {
    const showRunDetail = vi.fn();
    useSidePanelStore.setState({ showRunDetail });
    render(
      <FinaleStage
        model={makeModel()}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );

    expect(screen.queryByRole("button", { name: "裁决过程" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /主持人终审/ }));

    expect(showRunDetail).toHaveBeenCalledWith("m1", "moderator", "主持人");
  });

  it("无 moderatorRun 时标题退回纯文本", () => {
    render(
      <FinaleStage
        model={makeModel({ moderatorRunId: null })}
        execution={executionWith([])}
        messageId="m1"
      />,
    );

    expect(screen.getByText("主持人终审")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /主持人终审/ })).toBeNull();
  });

  it("wire 有模型字段时简报抬头署三方", () => {
    render(
      <FinaleStage
        model={settledBriefModel({
          moderatorModel: "deepseek/deepseek-v4-pro",
          moderatorOrigin: "platform",
          sides: [
            {
              key: "pro",
              name: "正方",
              stance: "pro",
              model: "doubao/seed-2.0",
              origin: "platform",
              is_subject: false,
            },
            {
              key: "con",
              name: "反方",
              stance: "con",
              model: "deepseek/deepseek-v4-flash",
              origin: "platform",
              is_subject: false,
            },
          ],
        })}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );
    expect(screen.getByTestId("debate-roster-line").textContent).toBe(
      "正方 豆包 · 反方 DeepSeek · 裁判 DeepSeek",
    );
  });

  it("wire 无模型字段时不展示跨模型署名", () => {
    render(
      <FinaleStage
        model={settledBriefModel()}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );
    expect(screen.queryByTestId("debate-roster-line")).toBeNull();
  });
});

describe("FinaleStage 三区布局", () => {
  it("正反：裁决卡纯判断（无争点/建议）+ 战果对照最强论点 +「留给你的」含建议与交接", () => {
    render(
      <FinaleStage
        model={settledBriefModel({
          rounds: [
            {
              roundNo: 1,
              focus: "成本",
              summary: "首轮交锋",
              verdict: null,
              sides: [],
              clashes: [],
              crossExam: [],
              witnessExam: [],
              userInterjections: [],
              inFlight: false,
              findings: [],
              threadTurns: [],
              scores: [
                {
                  sideKey: "pro",
                  name: "正方",
                  colorVar: "var(--debate-side-pro)",
                  argument: 4,
                  engagement: 3,
                  evidence: 3,
                  penalties: [],
                  note: "",
                  total: 10,
                },
                {
                  sideKey: "con",
                  name: "反方",
                  colorVar: "var(--debate-side-con)",
                  argument: 3,
                  engagement: 4,
                  evidence: 2,
                  penalties: ["以未证实的尾部风险当既定事实"],
                  note: "",
                  total: 8,
                },
              ],
            },
          ],
        })}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );

    expect(screen.getByText("倾向正方")).toBeTruthy();
    expect(screen.getByText("胜负手")).toBeTruthy();
    // 正反裁决卡不渲染争点；建议迁至「留给你的」
    expect(screen.queryByText("争点")).toBeNull();
    expect(screen.getByText(/建议：/)).toBeTruthy();
    expect(screen.getByText("先做试点")).toBeTruthy();
    expect(screen.getByText("战果对照")).toBeTruthy();
    expect(screen.getAllByText("最强论点").length).toBe(2);
    expect(screen.getByText("ROI 清晰")).toBeTruthy();
    expect(screen.getByText("风险未清")).toBeTruthy();
    // 三维常驻 + 罚分可展开
    expect(screen.getAllByText("论点").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("回应").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("证据").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/罚分 · 1/)).toBeTruthy();
    expect(screen.getByText("留给你的")).toBeTruthy();
    expect(screen.getByText(/要不要牺牲速度/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "回复拍板" })).toBeTruthy();
    expect(screen.getByText("实际成本")).toBeTruthy();
    expect(screen.getByRole("button", { name: "派查证" })).toBeTruthy();
    expect(screen.getByText(/只能等的：试点范围/)).toBeTruthy();
    expect(screen.queryByText(/需你定夺/)).toBeNull();
    expect(screen.queryByText("事实分歧")).toBeNull();
    expect(screen.queryByText("待解问题")).toBeNull();
  });

  it("回复拍板 / 派查证 预填主输入框 draft", async () => {
    const { useComposerDraftStore, draftKeyFor } = await import(
      "@/stores/composer"
    );
    const { useConversationStore } = await import("@/stores/conversation");
    useConversationStore.setState({ currentConversationId: "c-handoff" });
    useComposerDraftStore.setState({
      drafts: {},
      fillToken: 0,
    });

    render(
      <FinaleStage
        model={settledBriefModel()}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "回复拍板" }));
    expect(
      useComposerDraftStore.getState().drafts[draftKeyFor("c-handoff")]?.value,
    ).toBe("关于「要不要牺牲速度」，我的取舍是：");

    fireEvent.click(screen.getByRole("button", { name: "派查证" }));
    expect(
      useComposerDraftStore.getState().drafts[draftKeyFor("c-handoff")]?.value,
    ).toBe("关于「要不要牺牲速度」，我的取舍是：\n帮我查证：实际成本");
  });

  it("红队：裁决卡为方案评定（无加固建议），handoffs 空仍渲染「留给你的」加固建议 + 风险清单", () => {
    render(
      <FinaleStage
        model={settledBriefModel({
          form: "red_team",
          brief: {
            leaning: "方案可过，需补安全网",
            confidence: "medium",
            decisive: "",
            crux: "权限边界是否可接受",
            recommendation: "加熔断",
            strongest_points: {
              subject: "已有回滚",
              attacker: "权限过大",
            },
            handoffs: [],
          },
          sides: [
            {
              key: "subject",
              name: "方案方",
              stance: "",
              model: undefined,
              is_subject: true,
            },
            {
              key: "attacker",
              name: "红队",
              stance: "",
              model: undefined,
              is_subject: false,
            },
          ],
        })}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );

    expect(screen.getByText("方案评定")).toBeTruthy();
    // 红队裁决卡保留争点；加固建议在「留给你的」
    expect(screen.getByText("争点")).toBeTruthy();
    expect(screen.getByText("留给你的")).toBeTruthy();
    expect(screen.getByText(/加固建议：/)).toBeTruthy();
    expect(screen.getByText("加熔断")).toBeTruthy();
    expect(screen.getByText("风险清单")).toBeTruthy();
    expect(screen.getByText("方案方回应")).toBeTruthy();
    expect(screen.queryByText("战果对照")).toBeNull();
  });
});
