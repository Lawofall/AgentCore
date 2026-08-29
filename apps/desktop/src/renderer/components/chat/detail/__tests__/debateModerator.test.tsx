// @vitest-environment jsdom
/**
 * 辩论主持人侧面板：thinking 占位启发式、主持人识别、主持台账 live/收场两态。
 */

import type { AgentState, Execution, RunNode } from "@/stores/execution";
import type { DebateResultPayload } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildModeratorLedger,
  isDebateModeratorRun,
  isThinkingLivePlaceholder,
  resolveDebateModeratorRunId,
} from "../debateModerator";
import { RunModeratorLedger } from "../sections/RunModeratorLedger";

afterEach(cleanup);

function agent(
  partial: Partial<AgentState> & Pick<AgentState, "id">,
): AgentState {
  return {
    role: "角色",
    thinking: true,
    status: "working",
    currentRunId: partial.id,
    outputChunks: [],
    reasoningChunks: [],
    toolCalls: [],
    toolProgress: null,
    toolExecutionLive: null,
    ...partial,
  };
}

function run(
  partial: Partial<RunNode> & Pick<RunNode, "id" | "agentId">,
): RunNode {
  return {
    status: "running",
    task: "任务",
    dependsOn: [],
    parentRunId: null,
    kind: "agent",
    role: null,
    model: "m",
    usage: null,
    cost: null,
    error: null,
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    continuationIndex: 0,
    replacesRunId: null,
    revised: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    ...partial,
    sideKey: partial.sideKey ?? null,
  };
}

function baseExecution(overrides: Partial<Execution> = {}): Execution {
  return {
    id: "exec-1",
    planType: "debate",
    taskSummary: "该不该上",
    status: "running",
    agents: [],
    runs: [],
    progress: { completed: 0, total: 3 },
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
    ...overrides,
    acts: overrides.acts ?? [],
  };
}

const liveDebateRuns: RunNode[] = [
  run({ id: "mod", agentId: "a-mod", parentRunId: null, status: "running" }),
  run({
    id: "mod_r1_pro",
    agentId: "a-pro",
    parentRunId: "mod",
    stance: "pro",
    group: "debate:debate",
    round: 1,
    status: "running",
  }),
  run({
    id: "mod_r1_con",
    agentId: "a-con",
    parentRunId: "mod",
    stance: "con",
    group: "debate:debate",
    round: 1,
    status: "running",
  }),
];

describe("isThinkingLivePlaceholder", () => {
  it("thinking=false 不出「思考中」占位", () => {
    expect(
      isThinkingLivePlaceholder(
        agent({
          id: "a",
          thinking: false,
          status: "working",
          outputChunks: [],
        }),
      ),
    ).toBe(false);
  });

  it("thinking=true 且无输出时出占位", () => {
    expect(
      isThinkingLivePlaceholder(
        agent({ id: "a", thinking: true, status: "working", outputChunks: [] }),
      ),
    ).toBe(true);
  });

  it("已有输出或工具进度时不出占位", () => {
    expect(
      isThinkingLivePlaceholder(
        agent({
          id: "a",
          thinking: true,
          status: "working",
          outputChunks: ["hi"],
        }),
      ),
    ).toBe(false);
    expect(
      isThinkingLivePlaceholder(
        agent({
          id: "a",
          thinking: true,
          status: "working",
          toolProgress: { toolName: "web_search", chars: 12 },
        }),
      ),
    ).toBe(false);
  });
});

describe("resolveDebateModeratorRunId / isDebateModeratorRun", () => {
  it("进行中：从辩手 parentRunId 链识别主持人", () => {
    const execution = baseExecution({ runs: liveDebateRuns });
    expect(resolveDebateModeratorRunId(execution)).toBe("mod");
    expect(isDebateModeratorRun(execution, "mod")).toBe(true);
    expect(isDebateModeratorRun(execution, "mod_r1_pro")).toBe(false);
  });

  it("收场：以 debate.moderator_run_id 为准", () => {
    const debate = {
      execution_id: "exec-1",
      moderator_run_id: "mod-settled",
      form: "debate",
      motion: "题",
      stop_reason: "converged",
      narrative_first: false,
      sides: [],
      rounds: [],
      brief: {
        crux: "",
        strongest_points: {},
        leaning: "",
        confidence: "medium",
        recommendation: "",
      },
    } as DebateResultPayload;
    const execution = baseExecution({
      status: "completed",
      debate,
      runs: [
        run({
          id: "mod-settled",
          agentId: "a-mod",
          status: "completed",
        }),
      ],
    });
    expect(resolveDebateModeratorRunId(execution)).toBe("mod-settled");
    expect(isDebateModeratorRun(execution, "mod-settled")).toBe(true);
  });

  it("非辩论不识别主持人", () => {
    const execution = baseExecution({
      planType: "multi_agent",
      runs: [
        run({ id: "w1", agentId: "a1" }),
        run({ id: "w2", agentId: "a2", parentRunId: "w1" }),
      ],
    });
    expect(resolveDebateModeratorRunId(execution)).toBeNull();
  });
});

describe("buildModeratorLedger", () => {
  it("进行中：焦点+小结时间线，当前轮 inFlight，不含记分字段", () => {
    const execution = baseExecution({
      runs: liveDebateRuns,
      debateRounds: [
        {
          round_no: 1,
          focus: "拆分边界",
          summary: "",
          verdict: null,
          sides: [],
          clashes: [],
          cross_exam: [],
        },
      ],
    });
    const ledger = buildModeratorLedger(execution);
    expect(ledger).not.toBeNull();
    if (!ledger) throw new Error("expected ledger");
    expect(ledger.settled).toBe(false);
    expect(ledger.opening).toBeNull();
    expect(ledger.stopLabel).toBeNull();
    expect(ledger.rounds).toHaveLength(1);
    expect(ledger.rounds[0]).toMatchObject({
      roundNo: 1,
      focus: "拆分边界",
      summary: "",
      inFlight: true,
    });
    // 台账摘要形状不含 scores / 比分
    expect(ledger).not.toHaveProperty("scores");
    expect(JSON.stringify(ledger)).not.toMatch(/score|比分|记分/i);
  });

  it("进行中：首轮 sticky 开场白进入台账（发言前即可见）", () => {
    const execution = baseExecution({
      runs: liveDebateRuns,
      debateOpening: "各位，今天讨论微服务。",
      debateRounds: [
        {
          round_no: 1,
          focus: "拆分边界",
          summary: "",
          verdict: null,
          sides: [],
          clashes: [],
          cross_exam: [],
        },
      ],
    });
    const ledger = buildModeratorLedger(execution);
    expect(ledger).not.toBeNull();
    if (!ledger) throw new Error("expected ledger");
    expect(ledger.settled).toBe(false);
    expect(ledger.opening).toBe("各位，今天讨论微服务。");
  });

  it("收场：开场白 + 逐轮焦点/小结 + 收敛归因", () => {
    const debate = {
      execution_id: "exec-1",
      moderator_run_id: "mod",
      form: "debate",
      motion: "该不该上",
      stop_reason: "converged",
      opening: "各位，今天讨论微服务。",
      narrative_first: false,
      sides: [
        { key: "pro", name: "正方", stance: "支持", is_subject: false },
        { key: "con", name: "反方", stance: "反对", is_subject: false },
      ],
      rounds: [
        {
          round_no: 1,
          focus: "运维成本",
          summary: "双方围绕团队规模交锋。",
          verdict: {
            real_clash: true,
            new_arguments: false,
            converged: true,
            stop_reason: "converged",
            rationale: "已充分暴露",
          },
          sides: [
            { key: "pro", name: "正方", run_id: "mod_r1_pro", ok: true },
            { key: "con", name: "反方", run_id: "mod_r1_con", ok: true },
          ],
          clashes: [],
          scores: {
            pro: {
              argument: 3,
              engagement: 2,
              evidence: 2,
              penalties: [],
              note: "",
              total: 7,
            },
          },
        },
      ],
      brief: {
        crux: "团队规模",
        strongest_points: { pro: "扩展", con: "成本" },
        leaning: "暂缓",
        confidence: "medium",
        recommendation: "先单体",
      },
    } as DebateResultPayload;

    const ledger = buildModeratorLedger(
      baseExecution({
        status: "completed",
        debate,
        runs: liveDebateRuns.map((r) => ({ ...r, status: "completed" })),
      }),
    );
    expect(ledger).not.toBeNull();
    if (!ledger) throw new Error("expected ledger");
    expect(ledger.settled).toBe(true);
    expect(ledger.opening).toBe("各位，今天讨论微服务。");
    expect(ledger.stopLabel).toBe("已收敛");
    expect(ledger.rounds[0]).toMatchObject({
      roundNo: 1,
      focus: "运维成本",
      summary: "双方围绕团队规模交锋。",
      inFlight: false,
    });
    expect(JSON.stringify(ledger)).not.toMatch(/\b7\b.*argument|比分/);
  });
});

describe("RunModeratorLedger", () => {
  it("live 渲染焦点与进行中，不出现比分文案", () => {
    render(
      <RunModeratorLedger
        ledger={{
          settled: false,
          opening: null,
          stopLabel: null,
          rounds: [
            {
              roundNo: 1,
              focus: "拆分边界",
              summary: "",
              inFlight: true,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("主持台账")).toBeTruthy();
    expect(screen.getByText("第 1 轮")).toBeTruthy();
    expect(screen.getByText("进行中")).toBeTruthy();
    expect(screen.getByText(/拆分边界/)).toBeTruthy();
    expect(screen.queryByText(/比分|记分|得分/)).toBeNull();
  });

  it("收场渲染开场白、小结与收敛归因，并提供打开辩论室", () => {
    const onOpen = vi.fn();
    render(
      <RunModeratorLedger
        ledger={{
          settled: true,
          opening: "开场白正文",
          stopLabel: "已收敛",
          rounds: [
            {
              roundNo: 1,
              focus: "焦点甲",
              summary: "小结乙",
              inFlight: false,
            },
          ],
        }}
        onOpenDebateRoom={onOpen}
      />,
    );
    expect(screen.getByText(/开场白正文/)).toBeTruthy();
    expect(screen.getByText(/小结乙/)).toBeTruthy();
    expect(screen.getByText(/已收敛/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /打开辩论室/ }));
    expect(onOpen).toHaveBeenCalledOnce();
  });
});
