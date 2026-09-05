import { describe, expect, it } from "vitest";
import {
  type RunFrame,
  projectExecution,
  runInspectorSig,
} from "../../execution";
import { plan, started } from "./fixtures";

describe("runInspectorSig", () => {
  it("is stable when a sibling worker streams", () => {
    const base: RunFrame[] = [
      started("agent-1", "run-1"),
      started("agent-2", "run-2", 2),
      {
        t: 3,
        kind: "run_output_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "甲。",
      },
    ];
    const sibling: RunFrame[] = [
      ...base,
      {
        t: 4,
        kind: "run_output_delta",
        runId: "run-2",
        agentId: "agent-2",
        delta: "乙。",
      },
    ];
    const before = projectExecution(plan, base, "running");
    const after = projectExecution(plan, sibling, "running");
    expect(runInspectorSig(before, "run-1")).toBe(
      runInspectorSig(after, "run-1"),
    );
    expect(runInspectorSig(before, "run-2")).not.toBe(
      runInspectorSig(after, "run-2"),
    );
  });

  it("changes when this run's process grows", () => {
    const before = projectExecution(
      plan,
      [started("agent-1", "run-1")],
      "running",
    );
    const after = projectExecution(
      plan,
      [
        started("agent-1", "run-1"),
        {
          t: 2,
          kind: "run_reasoning_delta",
          runId: "run-1",
          agentId: "agent-1",
          delta: "想。",
        },
      ],
      "running",
    );
    expect(runInspectorSig(before, "run-1")).not.toBe(
      runInspectorSig(after, "run-1"),
    );
  });
});
