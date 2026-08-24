import {
  shouldShowTeamGraph,
  teamHasStartedRuns,
} from "@/components/chat/InlineTeamGraph";
import {
  type ExecutionPlan,
  type RunFrame,
  projectExecution,
} from "@/stores/execution";
import { describe, expect, it } from "vitest";

const plan: ExecutionPlan = {
  id: "exec-gate",
  planType: "multi_agent",
  taskSummary: "并行调研",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "w2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r2", agentId: "w2", task: "撰写", dependsOn: ["r1"] },
  ],
};

function started(runId: string, agentId: string, t = 1): RunFrame {
  return {
    t,
    kind: "run_started",
    agentId,
    runId,
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  };
}

function completed(runId: string, agentId: string, t = 2): RunFrame {
  return {
    t,
    kind: "run_completed",
    runId,
    agentId,
    outputSummary: "完成",
    durationMs: 100,
  };
}

describe("teamHasStartedRuns · inline graph gate", () => {
  it("开工挂起（paused + 全 pending）不渲染图", () => {
    const exec = projectExecution(plan, [], "paused");
    expect(exec.runs.every((r) => r.status === "pending")).toBe(true);
    expect(teamHasStartedRuns(exec.runs)).toBe(false);
  });

  it("开工即停止（cancelled + 从未启动 → 全 skipped）不渲染图", () => {
    const exec = projectExecution(plan, [], "cancelled");
    expect(exec.runs.every((r) => r.status === "skipped")).toBe(true);
    expect(teamHasStartedRuns(exec.runs)).toBe(false);
  });

  it("plan_review 波间挂起（已有完成节点）仍渲染图", () => {
    const frames: RunFrame[] = [started("r1", "w1"), completed("r1", "w1")];
    const exec = projectExecution(plan, frames, "paused");
    expect(exec.runs.find((r) => r.id === "r1")?.status).toBe("completed");
    expect(exec.runs.find((r) => r.id === "r2")?.status).toBe("pending");
    expect(teamHasStartedRuns(exec.runs)).toBe(true);
  });

  it("授权后续跑（running + 已有 run_started）渲染图", () => {
    const exec = projectExecution(plan, [started("r1", "w1")], "running");
    expect(teamHasStartedRuns(exec.runs)).toBe(true);
    expect(shouldShowTeamGraph(exec.runs)).toBe(true);
  });

  it("队员仍 pending 也渲染图（无开工卡闸）", () => {
    const exec = projectExecution(plan, [], "running");
    expect(teamHasStartedRuns(exec.runs)).toBe(false);
    expect(shouldShowTeamGraph(exec.runs)).toBe(true);
  });

  it("journal 回放 captain 已开、工人仍 pending → 出图（无开工卡闸）", () => {
    const withCaptain: ExecutionPlan = {
      ...plan,
      id: "exec-gate-captain",
      agents: [{ id: "ceo", role: "CEO" }, ...plan.agents],
      runs: [
        {
          id: "captain",
          agentId: "ceo",
          task: "",
          dependsOn: [],
          kind: "captain",
        },
        ...plan.runs,
      ],
    };
    const exec = projectExecution(
      withCaptain,
      [
        {
          t: 1,
          kind: "run_started",
          agentId: "ceo",
          runId: "captain",
          parentRunId: null,
          runKind: "captain",
          continuesRunId: null,
        },
      ],
      "paused",
    );
    expect(exec.runs.find((r) => r.id === "captain")?.status).toBe("running");
    expect(
      exec.runs
        .filter((r) => r.kind !== "captain")
        .every((r) => r.status === "pending"),
    ).toBe(true);
    expect(teamHasStartedRuns(exec.runs)).toBe(false);
    expect(shouldShowTeamGraph(exec.runs)).toBe(true);
  });
});
