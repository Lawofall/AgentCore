import {
  captainSynthesisPreviewText,
  coordinationWaitCaptainCaption,
  coordinationWaitLabel,
  isTeamSynthesizing,
  teamSynthesisPhaseLabel,
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
    debatePretrial: null,
    teamNotes: [],
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

  it("isTeamSynthesizing when all workers done and turn still running", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(isTeamSynthesizing(e)).toBe(true);
    expect(teamSynthesisPhaseLabel(e)).toBe("2/2 已完成，正在生成汇总");
  });

  it("not synthesizing while a worker is still running", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "running" }),
      ],
    });
    expect(isTeamSynthesizing(e)).toBe(false);
  });

  it("not synthesizing after turn completes", () => {
    const e = exec({
      status: "completed",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(isTeamSynthesizing(e)).toBe(false);
  });

  it("still synthesizing after CEO turn ended while harvest is live", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(isTeamSynthesizing(e, { turnTerminal: true })).toBe(true);
  });

  it("not synthesizing when CEO turn ended but a worker is still live", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "running" }),
      ],
    });
    expect(isTeamSynthesizing(e, { turnTerminal: true })).toBe(false);
  });

  it("not synthesizing when execution is paused (workers already done)", () => {
    const e = exec({
      status: "paused",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(isTeamSynthesizing(e)).toBe(false);
  });

  it("isTeamSynthesizing when workers are terminal beyond completed", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "failed" }),
        run({ id: "w3", status: "cancelled" }),
        run({ id: "w4", status: "skipped" }),
      ],
    });
    expect(isTeamSynthesizing(e)).toBe(true);
  });

  it("captainSynthesisPreviewText prefers draft body over headline", () => {
    expect(
      captainSynthesisPreviewText({
        execution_id: "e",
        completed: 2,
        total: 2,
        headline: "合成草稿更新 · 已完成 2/2",
        text: "两边方向一致：优先方案 A。",
        workers: [],
        in_progress: true,
      }),
    ).toBe("两边方向一致：优先方案 A。");
  });

  it("coordinationWaitLabel formats completed/total", () => {
    expect(coordinationWaitLabel(null)).toBeNull();
    expect(coordinationWaitLabel({ completed: 5, total: 8 })).toBe(
      "等待团队成员完成 (5/8)…",
    );
  });

  it("coordinationWaitLabel does not embed member roles", () => {
    expect(
      coordinationWaitLabel(
        { completed: 1, total: 2 },
        { waitingRoles: ["撰写员"] },
      ),
    ).toBe("等待团队成员完成 (1/2)…");
    expect(
      coordinationWaitLabel(
        { completed: 0, total: 2 },
        { waitingRoles: ["研究员", "撰写员"] },
      ),
    ).toBe("等待团队成员完成 (0/2)…");
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
