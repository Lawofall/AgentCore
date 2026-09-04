// @vitest-environment jsdom
/**
 * 终审区钻取惯例（全场统一「名字/身份行 = 打开 run 详情侧栏」）：
 * - 「主持人终审」标题在 moderatorRun 在时是钻取按钮，侧栏标题沿用「主持人」；
 * - 终审不挂模型徽章 / 三方署名（模型名只留记分牌）；
 * - 「裁决过程」文字链接已删（文字链接只留给就地展开）；
 * - moderatorRun 缺席（进行中 / 旧产物）时标题退回纯文本。
 *
 * 正反终审：裁决卡 + 留给你的（有交接则不展建议；不展最强论点）。
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
  it("标题是钻取按钮，不挂模型徽章，裁决过程链接已删", () => {
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
    expect(screen.queryByText("DeepSeek")).toBeNull();
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

  it("终审抬头不署三方模型名单", () => {
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
    expect(screen.queryByTestId("debate-roster-line")).toBeNull();
    expect(screen.queryByText(/正方 豆包/)).toBeNull();
    expect(screen.queryByText(/裁判 DeepSeek/)).toBeNull();
  });
});

describe("FinaleStage 终审布局", () => {
  it("正反：裁决卡 + 交接；有交接不展建议与最强论点", () => {
    render(
      <FinaleStage
        model={settledBriefModel()}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );

    expect(screen.getByText("倾向正方")).toBeTruthy();
    expect(screen.getByText("胜负手")).toBeTruthy();
    expect(screen.queryByText("争点")).toBeNull();
    expect(screen.queryByText(/建议：/)).toBeNull();
    expect(screen.queryByText("先做试点")).toBeNull();
    expect(screen.queryByText("战果对照")).toBeNull();
    expect(screen.queryByText("最强论点")).toBeNull();
    expect(screen.queryByText("ROI 清晰")).toBeNull();
    expect(screen.queryByText("风险未清")).toBeNull();
    expect(screen.queryByText("本轮记分")).toBeNull();
    expect(screen.queryByText(/净 /)).toBeNull();
    expect(screen.queryByText("你的倾向与 AI 一致")).toBeNull();
    expect(screen.queryByText("你的倾向与 AI 不同")).toBeNull();
    expect(screen.getByText("留给你的")).toBeTruthy();
    expect(screen.getByText(/要不要牺牲速度/)).toBeTruthy();
    expect(screen.getByText("实际成本")).toBeTruthy();
    expect(screen.getByText("还没核实")).toBeTruthy();
    expect(screen.getByText("只能等")).toBeTruthy();
    expect(screen.getByText("试点范围")).toBeTruthy();
    expect(screen.queryByText(/只能等的：/)).toBeNull();
    expect(screen.queryByRole("button", { name: "回复拍板" })).toBeNull();
    expect(screen.queryByRole("button", { name: "派查证" })).toBeNull();
    expect(screen.queryByText(/需你定夺/)).toBeNull();
    expect(screen.queryByText("事实分歧")).toBeNull();
    expect(screen.queryByText("待解问题")).toBeNull();
  });

  it("正反：倾向拆站队与反转，价值题拆对照，事实去掉机器串", () => {
    render(
      <FinaleStage
        model={settledBriefModel({
          brief: {
            leaning:
              "倾向反方：AI 人格论尚未证成；若未来实证证明能闭合缺口，则翻向正方。",
            confidence: "medium",
            decisive: "正方未回应归责如何操作",
            crux: "",
            recommendation: "不要出现",
            strongest_points: {},
            handoffs: [
              {
                kind: "value",
                text: "损失如何分担？选社会共担→正方；选部署者兜底→反方",
              },
              {
                kind: "fact",
                text: "EU 责任框架是否存在缺口（#e12, tier=unknown待评）【待核实】",
              },
            ],
          },
        })}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );
    expect(screen.getByText("倾向反方")).toBeTruthy();
    expect(screen.getByText("AI 人格论尚未证成")).toBeTruthy();
    expect(screen.getByText(/若未来实证证明能闭合缺口/)).toBeTruthy();
    expect(screen.getByText("损失如何分担？")).toBeTruthy();
    expect(screen.getByText("社会共担 → 正方")).toBeTruthy();
    expect(screen.getByText("部署者兜底 → 反方")).toBeTruthy();
    expect(screen.getByText("EU 责任框架是否存在缺口")).toBeTruthy();
    expect(screen.getByText("待核实")).toBeTruthy();
    expect(screen.queryByText(/tier=unknown/)).toBeNull();
    expect(screen.queryByText(/#e12/)).toBeNull();
    expect(screen.queryByText("不要出现")).toBeNull();
  });

  it("正反：无交接时降级展示建议，仍不展最强论点", () => {
    render(
      <FinaleStage
        model={settledBriefModel({
          brief: {
            leaning: "倾向正方",
            confidence: "high",
            decisive: "证据更扎实",
            crux: "成本可否接受",
            recommendation: "先做试点",
            strongest_points: { pro: "ROI 清晰", con: "风险未清" },
            handoffs: [],
          },
        })}
        execution={executionWith([moderatorRun()])}
        messageId="m1"
      />,
    );
    expect(screen.getByText(/建议：/)).toBeTruthy();
    expect(screen.getByText("先做试点")).toBeTruthy();
    expect(screen.queryByText("最强论点")).toBeNull();
    expect(screen.queryByText("ROI 清晰")).toBeNull();
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
