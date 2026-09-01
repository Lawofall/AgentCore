import { beforeEach, describe, expect, it } from "vitest";
import {
  type ExecutionPlan,
  type RunFrame,
  execRuntime,
  hasUnsettledRuns,
  useExecutionStore,
} from "../../execution";

// hasUnsettledRuns drives the message_end「后台托管继续跑」hold: true = still has a
// pending/running **worker** (keep the graph running), false = every worker terminal
// OR no workers to wait on (let message_end 收口). Captain pending does NOT hold —
// its early run_started is often dropped pre-plan; append-turn captains likewise.
// NOT the exact negation of the private runsAllSettled reconcile check — both are
// false on a 0-run / captain-only graph; both exclude kind=captain.

const MID = "m";
const store = () => useExecutionStore.getState();
const rt = () => execRuntime(store(), MID);

const onePlan: ExecutionPlan = {
  id: "e1",
  planType: "multi_agent",
  taskSummary: "t",
  agents: [{ id: "a1", role: "r" }],
  runs: [{ id: "r1", agentId: "a1", task: "t", dependsOn: [] }],
};

const captainPlusWorker: ExecutionPlan = {
  id: "e2",
  planType: "multi_agent",
  taskSummary: "t",
  agents: [
    { id: "cap", role: "CEO" },
    { id: "a1", role: "r" },
  ],
  runs: [
    { id: "cap", agentId: "cap", task: "", dependsOn: [], kind: "captain" },
    { id: "r1", agentId: "a1", task: "t", dependsOn: [] },
  ],
};

/** Plan + graph_append style second captain (extra kind=captain still pending). */
const twoCaptainsPlusWorker: ExecutionPlan = {
  id: "e3",
  planType: "multi_agent",
  taskSummary: "t",
  agents: [
    { id: "cap", role: "CEO" },
    { id: "a1", role: "r" },
    { id: "cap2", role: "CEO" },
  ],
  runs: [
    { id: "cap", agentId: "cap", task: "", dependsOn: [], kind: "captain" },
    { id: "r1", agentId: "a1", task: "t", dependsOn: [] },
    {
      id: "cap2",
      agentId: "cap2",
      task: "",
      dependsOn: [],
      kind: "captain",
    },
  ],
};

function started(runId = "r1", agentId = "a1"): RunFrame {
  return {
    t: 1,
    kind: "run_started",
    agentId,
    runId,
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  };
}

function completed(runId = "r1", agentId = "a1"): RunFrame {
  return {
    t: 2,
    kind: "run_completed",
    runId,
    agentId,
    outputSummary: "ok",
    durationMs: 1,
  };
}

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("hasUnsettledRuns", () => {
  it("is false when the slot has no plan", () => {
    expect(hasUnsettledRuns(rt())).toBe(false);
  });

  it("is true while a plan-declared run is still pending", () => {
    store().startExecution(onePlan, MID);
    expect(hasUnsettledRuns(rt())).toBe(true);
  });

  it("is true while a run is running", () => {
    store().startExecution(onePlan, MID);
    store().recordFrame(started(), MID);
    expect(hasUnsettledRuns(rt())).toBe(true);
  });

  it("is false once every run reached a terminal state", () => {
    store().startExecution(onePlan, MID);
    store().recordFrame(started(), MID);
    store().recordFrame(completed(), MID);
    expect(hasUnsettledRuns(rt())).toBe(false);
  });

  it("is false for a plan that declares no runs (nothing in flight to wait on)", () => {
    store().startExecution({ ...onePlan, runs: [] }, MID);
    expect(hasUnsettledRuns(rt())).toBe(false);
  });

  it("is false when only the captain remains pending after workers completed", () => {
    store().startExecution(captainPlusWorker, MID);
    store().recordFrame(started("r1", "a1"), MID);
    store().recordFrame(completed("r1", "a1"), MID);
    // Captain never got run_started / run_completed folded (common pre-plan drop).
    expect(hasUnsettledRuns(rt())).toBe(false);
  });

  it("is true when a worker is still pending even if captain looks settled", () => {
    store().startExecution(captainPlusWorker, MID);
    store().recordFrame(started("cap", "cap"), MID);
    store().recordFrame(completed("cap", "cap"), MID);
    // Worker r1 still pending.
    expect(hasUnsettledRuns(rt())).toBe(true);
  });

  it("is false when only an extra append-captain remains pending", () => {
    store().startExecution(twoCaptainsPlusWorker, MID);
    store().recordFrame(started("r1", "a1"), MID);
    store().recordFrame(completed("r1", "a1"), MID);
    expect(hasUnsettledRuns(rt())).toBe(false);
  });
});

describe("runsAllSettled reconcile (via recordFrame → setStatus completed)", () => {
  it("settles when workers completed even if a second captain stays pending", () => {
    store().startExecution(twoCaptainsPlusWorker, MID);
    store().recordFrame(started("r1", "a1"), MID);
    expect(rt().status).toBe("running");
    store().recordFrame(completed("r1", "a1"), MID);
    // Ghost append-captain never got frames — must not pin execution running.
    expect(hasUnsettledRuns(rt())).toBe(false);
    expect(rt().status).toBe("completed");
  });

  it("does not settle while a worker is still pending", () => {
    store().startExecution(twoCaptainsPlusWorker, MID);
    store().recordFrame(started("cap", "cap"), MID);
    store().recordFrame(completed("cap", "cap"), MID);
    expect(hasUnsettledRuns(rt())).toBe(true);
    expect(rt().status).toBe("running");
  });

  it("does not settle from output-delta recordFrames (structural frames own 收口)", () => {
    store().startExecution(onePlan, MID);
    store().recordFrame(started(), MID);
    store().recordFrames(
      [
        {
          t: 2,
          kind: "run_output_delta",
          runId: "r1",
          agentId: "a1",
          delta: "token ",
        },
        {
          t: 3,
          kind: "run_output_delta",
          runId: "r1",
          agentId: "a1",
          delta: "storm",
        },
      ],
      MID,
    );
    expect(rt().status).toBe("running");
    expect(hasUnsettledRuns(rt())).toBe(true);
    store().recordFrame(completed(), MID);
    expect(rt().status).toBe("completed");
  });
});
