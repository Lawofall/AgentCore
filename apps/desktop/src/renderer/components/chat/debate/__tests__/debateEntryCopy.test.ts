import type { Execution, RunNode } from "@/stores/execution";
import type { DebateResultPayload } from "@/types/events";
import { describe, expect, it } from "vitest";
import {
  debateConclusionHook,
  debatePreviewSubtitle,
} from "../debateEntryCopy";

function run(
  partial: Partial<RunNode> & Pick<RunNode, "id" | "agentId">,
): RunNode {
  return {
    status: "completed",
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
    status: "completed",
    agents: [],
    runs: [
      run({ id: "mod", agentId: "a-mod" }),
      run({
        id: "pro",
        agentId: "a-pro",
        stance: "pro",
        group: "debate:debate",
        round: 1,
      }),
    ],
    progress: { completed: 2, total: 2 },
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

const settledDebate: DebateResultPayload = {
  execution_id: "exec-1",
  moderator_run_id: "mod",
  form: "debate",
  motion: "是否上线",
  stop_reason: "converged",
  narrative_first: false,
  sides: [
    { key: "pro", name: "正方", stance: "支持", is_subject: false },
    { key: "con", name: "反方", stance: "反对", is_subject: false },
  ],
  rounds: [
    {
      round_no: 1,
      focus: "成本",
      summary: "仍有分歧",
      sides: [],
      clashes: [],
      verdict: {
        real_clash: true,
        converged: true,
        rationale: "",
        new_arguments: false,
        stop_reason: "converged",
      },
      user_interjections: [],
      cross_exam: [],
      scores: {},
    },
  ],
  brief: {
    crux: "",
    strongest_points: {},
    leaning: "倾向暂缓上线",
    confidence: "high",
    recommendation: "",
  },
  closings: [],
  opening: undefined,
};

describe("debatePreviewSubtitle", () => {
  it("live shows current round", () => {
    const execution = baseExecution({
      status: "running",
      debate: null,
      debateRounds: [
        {
          round_no: 2,
          focus: "窗口",
          summary: "",
          verdict: null,
          sides: [],
          clashes: [],
          cross_exam: [],
        },
      ],
      runs: [
        run({ id: "mod", agentId: "a-mod", status: "running" }),
        run({
          id: "mod_r2_pro",
          agentId: "a-pro",
          status: "running",
          stance: "pro",
          group: "debate:debate",
          round: 2,
        }),
        run({
          id: "mod_r2_con",
          agentId: "a-con",
          status: "running",
          stance: "con",
          group: "debate:debate",
          round: 2,
        }),
      ],
    });
    expect(debatePreviewSubtitle(execution)).toMatch(/第 2 轮进行中/);
  });

  it("settled prefers brief leaning · confidence", () => {
    const execution = baseExecution({ debate: settledDebate });
    expect(debatePreviewSubtitle(execution)).toBe("倾向暂缓上线 · 置信高");
    const hook = debateConclusionHook(execution);
    expect(hook?.leaning).toBe("倾向暂缓上线");
    expect(hook?.confidenceLabel).toBe("高");
  });
});
