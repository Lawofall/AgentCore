// @vitest-environment jsdom
/**
 * 辩论主持人侧面板：thinking 占位启发式、主持人识别。
 */

import type { AgentState, Execution, RunNode } from "@/stores/execution";
import type { DebateResultPayload } from "@/types/events";
import { describe, expect, it } from "vitest";
import {
  isDebateModeratorRun,
  isThinkingLivePlaceholder,
  resolveDebateModeratorRunId,
} from "../debateModerator";

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
