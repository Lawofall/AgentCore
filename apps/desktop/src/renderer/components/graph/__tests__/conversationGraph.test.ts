import type { Execution, RunNode } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { buildGraphScene } from "../scene";
import { foldConversationGraphExecutions } from "../conversationGraph";

function run(partial: Pick<RunNode, "id"> & Partial<RunNode>): RunNode {
  return {
    agentId: partial.id,
    task: partial.id,
    status: "completed",
    dependsOn: [],
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    error: null,
    parentRunId: null,
    kind: "agent",
    role: null,
    model: null,
    reasoningEffort: null,
    usage: null,
    cost: null,
    stance: null,
    group: null,
    round: 0,
    sideKey: null,
    continuesRunId: null,
    continuationIndex: 0,
    replacesRunId: null,
    revised: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    ...partial,
  };
}

function execution(runs: RunNode[], extra?: Partial<Execution>): Execution {
  return {
    id: "turn",
    planType: "multi_agent",
    taskSummary: "t",
    status: "completed",
    agents: [],
    runs,
    acts: [],
    progress: { completed: runs.length, total: runs.length },
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    ...extra,
  };
}

describe("foldConversationGraphExecutions", () => {
  it("joins later segments onto the conclusions they depend on", () => {
    const folded = foldConversationGraphExecutions([
      {
        messageId: "m1",
        execution: execution([
          run({ id: "bookend", kind: "captain", task: "" }),
          run({ id: "open-1", kind: "agent", task: "开头" }),
          run({ id: "write", dependsOn: ["open-1"], task: "写" }),
        ]),
      },
      {
        messageId: "m2",
        execution: execution([
          run({
            id: "open-2",
            kind: "agent",
            task: "接着",
            dependsOn: ["write"],
          }),
        ]),
      },
    ]);

    expect(folded.execution?.runs.map((item) => item.id)).toEqual([
      "open-1",
      "write",
      "open-2",
    ]);
    expect(folded.execution?.runs.every((item) => item.kind === "agent")).toBe(
      true,
    );
    expect(folded.owners.get("open-2")).toBe("m2");

    const scene = buildGraphScene(folded.execution!);
    expect(scene.nodeIds).toEqual(["open-1", "write", "open-2"]);
    expect(scene.edges.map((edge) => `${edge.source}->${edge.target}`)).toEqual(
      ["open-1->write", "write->open-2"],
    );
  });

  it("leaves a debate turn off this graph", () => {
    const folded = foldConversationGraphExecutions([
      {
        messageId: "m1",
        execution: execution([run({ id: "open-1", kind: "agent" })]),
      },
      {
        messageId: "m-debate",
        execution: execution([run({ id: "mod", stance: "pro", group: "pro" })], {
          debate: { execution_id: "d" } as Execution["debate"],
        }),
      },
    ]);
    expect(folded.execution?.runs.map((item) => item.id)).toEqual(["open-1"]);
  });
});
