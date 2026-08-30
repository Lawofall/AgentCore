import type { Execution } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { isLiveUnsettledDebate } from "../liveDebateSteer";

function execution(overrides: Partial<Execution> = {}): Execution {
  return {
    id: "exec-d",
    planType: "debate",
    taskSummary: "该不该上",
    status: "running",
    agents: [],
    runs: [
      {
        id: "r-pro",
        agentId: "a-pro",
        status: "running",
        task: "立论",
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
        stance: "pro",
        group: "debate:debate",
        round: 1,
        continuesRunId: null,
        continuationIndex: 0,
        replacesRunId: null,
        revised: null,
        checkpoint: null,
        receivedContext: [],
        escalations: [],
        process: [],
        sideKey: null,
      },
    ],
    progress: { completed: 0, total: 1 },
    acts: [],
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
    ...overrides,
  };
}

describe("isLiveUnsettledDebate", () => {
  it("is true for isDebate + 未收场 + running", () => {
    expect(isLiveUnsettledDebate(execution())).toBe(true);
  });

  it("is true while paused (进行中未硬停)", () => {
    expect(isLiveUnsettledDebate(execution({ status: "paused" }))).toBe(true);
  });

  it("is false once debate_result settles the turn", () => {
    expect(
      isLiveUnsettledDebate(
        execution({
          status: "completed",
          debate: { execution_id: "exec-d" } as Execution["debate"],
        }),
      ),
    ).toBe(false);
  });

  it("is false after Stop / cancel so the composer can talk to CEO again", () => {
    expect(isLiveUnsettledDebate(execution({ status: "cancelled" }))).toBe(
      false,
    );
  });

  it("is false for an ordinary team turn", () => {
    expect(
      isLiveUnsettledDebate(
        execution({
          planType: "multi_agent",
          runs: [],
        }),
      ),
    ).toBe(false);
  });
});
