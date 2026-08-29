import {
  agentNodeLiveSig,
  deriveAgentNodeLive,
} from "@/components/graph/graphLive";
import * as reviewConcern from "@/lib/reviewConcern";
import type { AgentState, Execution, RunNode } from "@/stores/execution";
import { describe, expect, it, vi } from "vitest";

function run(
  partial: Partial<RunNode> & Pick<RunNode, "id" | "status" | "agentId">,
): RunNode {
  return {
    task: "t",
    dependsOn: [],
    parentRunId: null,
    kind: "agent",
    role: null,
    model: null,
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
    sideKey: null,
    ...partial,
  };
}

function agent(
  partial: Partial<AgentState> & Pick<AgentState, "id" | "role">,
): AgentState {
  return {
    thinking: false,
    status: "idle",
    currentRunId: null,
    outputChunks: [],
    reasoningChunks: [],
    toolCalls: [],
    toolProgress: null,
    toolExecutionLive: null,
    ...partial,
  };
}

function exec(partial: {
  agents: AgentState[];
  runs: RunNode[];
  status?: Execution["status"];
}): Execution {
  return {
    id: "e1",
    planType: "multi_agent",
    taskSummary: "并行调研",
    status: partial.status ?? "running",
    agents: partial.agents,
    runs: partial.runs,
    progress: {
      completed: partial.runs.filter((r) => r.status === "completed").length,
      total: partial.runs.length,
    },
    acts: [],
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
  };
}

const deriveOpts = {
  scene: null,
  litRunId: null,
  enterIndex: 0,
  unitExpanded: false,
};

describe("agentNodeLiveSig", () => {
  it("stays equal when another agent streams deltas", () => {
    const base = exec({
      agents: [
        agent({
          id: "a1",
          role: "研究员",
          status: "completed",
          outputChunks: ["done"],
        }),
        agent({
          id: "a2",
          role: "分析师",
          status: "working",
          currentRunId: "r2",
          outputChunks: ["x"],
        }),
      ],
      runs: [
        run({ id: "r1", agentId: "a1", status: "completed" }),
        run({ id: "r2", agentId: "a2", status: "running" }),
      ],
    });
    const idleSig = agentNodeLiveSig(base, "r1");
    const next: Execution = {
      ...base,
      agents: base.agents.map((a) =>
        a.id === "a2"
          ? { ...a, outputChunks: [...a.outputChunks, "more-tokens-here"] }
          : a,
      ),
    };
    expect(agentNodeLiveSig(next, "r1")).toBe(idleSig);
    expect(agentNodeLiveSig(next, "r2")).not.toBe(agentNodeLiveSig(base, "r2"));
  });

  it("changes when tool_use_end flips status without changing toolCalls.length", () => {
    const runningTc = {
      id: "tc1",
      toolName: "read_file",
      arguments: {},
      result: null,
      status: "running" as const,
    };
    const base = exec({
      agents: [
        agent({
          id: "a1",
          role: "研究员",
          status: "working",
          currentRunId: "r1",
          toolCalls: [runningTc],
        }),
      ],
      runs: [run({ id: "r1", agentId: "a1", status: "running" })],
    });
    const before = agentNodeLiveSig(base, "r1");
    const agent0 = base.agents[0];
    expect(agent0).toBeDefined();
    if (!agent0) throw new Error("expected agent");
    const after: Execution = {
      ...base,
      agents: [
        {
          ...agent0,
          toolCalls: [
            {
              ...runningTc,
              status: "success",
              result: "ok",
            },
          ],
        },
      ],
    };
    expect(agentNodeLiveSig(after, "r1")).not.toBe(before);
  });
});

describe("deriveAgentNodeLive", () => {
  it("builds outputPreview from chunk tails without needing a full join shape", () => {
    const prefix = "α".repeat(200);
    const tail = "最新一句在末尾可见";
    const execution = exec({
      agents: [
        agent({
          id: "a1",
          role: "研究员",
          status: "working",
          currentRunId: "r1",
          outputChunks: [prefix, tail],
        }),
      ],
      runs: [run({ id: "r1", agentId: "a1", status: "running" })],
    });
    const faceRun = execution.runs[0];
    expect(faceRun).toBeDefined();
    if (!faceRun) throw new Error("expected run");
    const face = deriveAgentNodeLive(execution, faceRun, deriveOpts);
    expect(face.outputPreview).toContain(tail);
    expect(face.outputPreview.length).toBeLessThanOrEqual(81);
    expect(face.tokenCount).toBeGreaterThan(0);
  });

  it("does not scan review concern for non-review roles", () => {
    const spy = vi.spyOn(reviewConcern, "detectReviewConcern");
    const text = "综合评分 4/10，整体方向偏了，建议重写。";
    const execution = exec({
      agents: [
        agent({
          id: "a1",
          role: "研究员",
          status: "completed",
          outputChunks: [text],
        }),
      ],
      runs: [run({ id: "r1", agentId: "a1", status: "completed" })],
    });
    const faceRun = execution.runs[0];
    expect(faceRun).toBeDefined();
    if (!faceRun) throw new Error("expected run");
    const face = deriveAgentNodeLive(execution, faceRun, deriveOpts);
    expect(face.reviewConcern).toBeNull();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("scans review concern for review-like roles when completed", () => {
    const execution = exec({
      agents: [
        agent({
          id: "a1",
          role: "学术审校员",
          status: "completed",
          outputChunks: ["综合评分 4/10，问题较多需要修改。"],
        }),
      ],
      runs: [run({ id: "r1", agentId: "a1", status: "completed" })],
    });
    const faceRun = execution.runs[0];
    expect(faceRun).toBeDefined();
    if (!faceRun) throw new Error("expected run");
    const face = deriveAgentNodeLive(execution, faceRun, deriveOpts);
    expect(face.reviewConcern).toBe("critical");
  });
});
