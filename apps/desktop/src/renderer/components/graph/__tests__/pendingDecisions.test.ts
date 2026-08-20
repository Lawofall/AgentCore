import type { Execution, RunNode } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import {
  collectGraphPendingDecisions,
  graphPendingDecisionsLiveSig,
} from "../pendingDecisions";

function run(partial: Partial<RunNode> & { id: string }): RunNode {
  return {
    agentId: partial.id,
    task: "",
    status: "running",
    dependsOn: [],
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    error: null,
    failureKind: null,
    productLanded: null,
    parentRunId: null,
    kind: "agent",
    role: null,
    model: null,
    usage: null,
    cost: null,
    stance: null,
    group: null,
    round: 0,
    sideKey: null,
    continuesRunId: null,
    continuationIndex: 0,
    revised: null,
    replacesRunId: null,
    actId: "act-1",
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    ...partial,
  } as RunNode;
}

function execution(runs: RunNode[], agents: { id: string; role: string }[]) {
  return {
    id: "exec1",
    planType: "multi_agent",
    taskSummary: "T",
    status: "running",
    agents: agents.map((a) => ({ ...a })),
    runs,
    acts: [],
  } as unknown as Execution;
}

describe("collectGraphPendingDecisions", () => {
  it("returns [] with no execution", () => {
    expect(collectGraphPendingDecisions(null)).toEqual([]);
    expect(collectGraphPendingDecisions(undefined)).toEqual([]);
  });

  it("collects pending escalations anchored to their run + act, with role label", () => {
    const exec = execution(
      [
        run({
          id: "r1",
          actId: "act-2",
          escalations: [
            {
              id: "e1",
              question: "q",
              assumption: "a",
              blocking: true,
              status: "pending",
              answer: null,
              kind: "dep",
              questions: [],
            },
          ],
        }),
      ],
      [{ id: "r1", role: "调研" }],
    );
    const out = collectGraphPendingDecisions(exec);
    expect(out).toEqual([
      {
        id: "esc:e1",
        kind: "escalation",
        runId: "r1",
        actId: "act-2",
        title: "调研",
        detail: "待你拍板（缺输入）",
      },
    ]);
  });

  it("collects pending checkpoints and ignores resolved escalations/checkpoints", () => {
    const exec = execution(
      [
        run({
          id: "r1",
          checkpoint: { status: "pending", decision: null },
          escalations: [
            {
              id: "e0",
              question: "q",
              assumption: "a",
              blocking: false,
              status: "resolved",
              answer: "ok",
              kind: "normal",
              questions: [],
            },
          ],
        }),
        run({
          id: "r2",
          checkpoint: { status: "resolved", decision: "continue" },
        }),
      ],
      [{ id: "r1", role: "撰写" }],
    );
    const out = collectGraphPendingDecisions(exec);
    expect(out).toEqual([
      {
        id: "cp:r1",
        kind: "checkpoint",
        runId: "r1",
        actId: "act-1",
        title: "撰写",
        detail: "待放行",
      },
    ]);
  });

  it("excludes the captain run from node-anchored decisions", () => {
    const exec = execution(
      [
        run({
          id: "cap",
          kind: "captain",
          checkpoint: { status: "pending", decision: null },
        }),
        run({ id: "r1", checkpoint: { status: "pending", decision: null } }),
      ],
      [{ id: "r1", role: "撰写" }],
    );
    const out = collectGraphPendingDecisions(exec);
    expect(out.map((d) => d.runId)).toEqual(["r1"]);
  });

  it("anchors tool approval to the captain (or null when absent)", () => {
    const withCaptain = execution(
      [run({ id: "cap", kind: "captain" }), run({ id: "r1" })],
      [],
    );
    expect(
      collectGraphPendingDecisions(withCaptain, [
        { kind: "approval", id: "tc1" },
      ]),
    ).toEqual([
      {
        id: "approval:tc1",
        kind: "approval",
        runId: "cap",
        actId: null,
        title: "工具审批",
        detail: "待放行",
      },
    ]);
  });

  it("orders node-anchored decisions before execution-level ones", () => {
    const exec = execution(
      [
        run({
          id: "r1",
          escalations: [
            {
              id: "e1",
              question: "q",
              assumption: "a",
              blocking: true,
              status: "pending",
              answer: null,
              kind: "normal",
              questions: [],
            },
          ],
        }),
      ],
      [{ id: "r1", role: "调研" }],
    );
    const out = collectGraphPendingDecisions(exec, [
      { kind: "approval", id: "tc1" },
    ]);
    expect(out.map((d) => d.kind)).toEqual(["escalation", "approval"]);
  });

  it("does not stamp a second hot-gate kind with the approval title", () => {
    const exec = execution(
      [run({ id: "cap", kind: "captain" }), run({ id: "r1" })],
      [],
    );
    const out = collectGraphPendingDecisions(exec, [
      { kind: "approval", id: "a1" },
      { kind: "synthetic_hot_gate", id: "b1" },
    ]);
    const titles = out.map((d) => d.title);
    expect(titles).toEqual(["工具审批", "synthetic_hot_gate"]);
    expect(new Set(titles).size).toBe(2);
    expect(out[1]?.kind).not.toBe("approval");
    expect(out.map((d) => d.id)).toEqual([
      "approval:a1",
      "synthetic_hot_gate:b1",
    ]);
  });
});

describe("graphPendingDecisionsLiveSig", () => {
  it("ignores streaming-stable runs and flips when escalation becomes pending", () => {
    const base = execution(
      [
        run({ id: "captain", kind: "captain", agentId: "ceo" }),
        run({ id: "r1", agentId: "a1", escalations: [] }),
      ],
      [{ id: "a1", role: "调研" }],
    );
    const streamed = execution(
      [
        run({ id: "captain", kind: "captain", agentId: "ceo" }),
        run({ id: "r1", agentId: "a1", escalations: [] }),
      ],
      [{ id: "a1", role: "调研" }],
    );
    expect(graphPendingDecisionsLiveSig(base)).toBe(
      graphPendingDecisionsLiveSig(streamed),
    );

    const withEsc = execution(
      [
        run({ id: "captain", kind: "captain", agentId: "ceo" }),
        run({
          id: "r1",
          agentId: "a1",
          escalations: [
            {
              id: "e1",
              question: "q",
              assumption: "a",
              blocking: true,
              status: "pending",
              answer: null,
              kind: "normal",
              questions: [],
            },
          ],
        }),
      ],
      [{ id: "a1", role: "调研" }],
    );
    expect(graphPendingDecisionsLiveSig(withEsc)).not.toBe(
      graphPendingDecisionsLiveSig(base),
    );
    expect(graphPendingDecisionsLiveSig(withEsc)).toContain("r1:1:0");
  });
});
