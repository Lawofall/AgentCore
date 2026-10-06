import {
  coordinationWaitCaptainCaption,
  graphProgress,
  waitingWorkerRoles,
  workerProgress,
} from "@/components/chat/teamSynthesisPhase";
import type { Execution, RunNode } from "@/stores/execution";
import { describe, expect, it } from "vitest";

function run(
  partial: Partial<RunNode> & Pick<RunNode, "id" | "status">,
): RunNode {
  return {
    agentId: partial.id,
    task: "t",
    dependsOn: [],
    parentRunId: null,
    kind: "agent",
    role: null,
    model: null,
    reasoningEffort: null,
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

function exec(partial: {
  status: Execution["status"];
  runs: RunNode[];
}): Execution {
  return {
    id: "e1",
    planType: "multi_agent",
    taskSummary: "并行调研",
    status: partial.status,
    agents: [],
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
  };
}

describe("teamSynthesisPhase", () => {
  it("workerProgress excludes captain", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(workerProgress(e)).toEqual({ completed: 2, total: 2 });
  });

  it("workerProgress folds same-person continuation into one seat", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "cancelled" }),
        run({
          id: "w1b",
          status: "completed",
          continuesRunId: "w1",
          continuationIndex: 1,
        }),
      ],
    });
    expect(workerProgress(e)).toEqual({ completed: 1, total: 1 });
    expect(graphProgress(e)).toEqual({ completed: 1, total: 2 });
  });

  it("coordinationWaitCaptainCaption stays short without elapsed", () => {
    expect(
      coordinationWaitCaptainCaption(
        { completed: 1, total: 2 },
        { waitingRoles: ["撰写员"] },
      ),
    ).toBe("等待「撰写员」(1/2)");
    expect(
      coordinationWaitCaptainCaption(
        { completed: 1, total: 2 },
        { waitingRoles: ["研究员", "撰写员"] },
      ),
    ).toBe("等待团队 (1/2)");
    expect(
      coordinationWaitCaptainCaption(
        { completed: 1, total: 1 },
        { waitingRoles: [] },
      ),
    ).toBeNull();
  });

  it("waitingWorkerRoles lists outstanding workers", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "r1", status: "completed", role: "研究员" }),
        run({ id: "r2", status: "running", role: "撰写员" }),
      ],
    });
    expect(waitingWorkerRoles(e)).toEqual(["撰写员"]);
  });
});
