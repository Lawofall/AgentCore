/**
 * Document/Live gate: streaming deltas must not replace RF nodes/edges refs.
 */
import type { Execution } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { INPUT_ID } from "../constants";
import {
  graphDocumentFingerprint,
  graphShellSnapshotKey,
} from "../graphDocument";
import { projectTurnGraph } from "../projectTurnGraph";
import { buildGraphScene } from "../scene";

function minimalExec(output = ""): Execution {
  return {
    id: "e1",
    planType: "multi_agent",
    taskSummary: "并行调研",
    status: "running",
    agents: [
      {
        id: "w1",
        role: "member",
        thinking: true,
        status: "working",
        outputChunks: output ? [output] : [],
        reasoningChunks: [],
        toolCalls: [],
        toolProgress: null,
        toolExecutionLive: null,
        currentRunId: "w1",
      },
    ],
    runs: [
      {
        id: "captain",
        agentId: "ceo",
        task: "",
        status: "pending",
        dependsOn: ["w1"],
        outputSummary: null,
        outputFiles: [],
        debrief: null,
        durationMs: null,
        startedAt: null,
        error: null,
        parentRunId: null,
        kind: "captain",
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
        replacesRunId: null,
        revised: null,
        checkpoint: null,
        receivedContext: [],
        escalations: [],
        process: [],
      },
      {
        id: "w1",
        agentId: "w1",
        task: "调研",
        status: "running",
        dependsOn: [],
        outputSummary: null,
        outputFiles: [],
        debrief: null,
        durationMs: null,
        startedAt: 1,
        error: null,
        parentRunId: null,
        kind: "agent",
        role: "member",
        model: null,
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
      },
    ],
    progress: { completed: 0, total: 2 },
    acts: [],
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
  };
}

const positions = {
  [INPUT_ID]: { x: 0, y: 0 },
  captain: { x: 0, y: 200 },
  w1: { x: 0, y: 100 },
};

function projectShell(execution: Execution) {
  const scene = buildGraphScene(execution);
  return projectTurnGraph({
    execution,
    scene,
    positions,
    nodeSizes: {},
    groups: [],
    bbox: { width: 400, height: 400 },
    actCards: [],
    edges: [
      { id: "in->w1", source: INPUT_ID, target: "w1", kind: "dep" },
      { id: "w1->cap", source: "w1", target: "captain", kind: "dep" },
    ],
    handleDirection: "vertical",
    litRunId: null,
    litEndpointMessageId: null,
    captainRun: { id: "captain" },
    captainStatus: null,
    finalAnswer: null,
    taskMessage: null,
    activateNode: () => undefined,
    expandedUnits: new Set(),
    injectOverlay: null,
    layoutKind: "leftright",
    onFocusAct: () => undefined,
    documentShell: true,
  });
}

describe("Document/Live · delta does not replace nodes/edges refs", () => {
  it("fingerprint stable across output-only deltas", () => {
    const a = minimalExec("hello");
    const b = minimalExec("hello world more tokens");
    b.agents[0].outputChunks = ["hello world more tokens"];
    const fa = graphDocumentFingerprint({
      execution: a,
      expandedUnits: new Set(),
      focusedActId: null,
      handleDirection: "vertical",
    });
    const fb = graphDocumentFingerprint({
      execution: b,
      expandedUnits: new Set(),
      focusedActId: null,
      handleDirection: "vertical",
    });
    expect(fa).toBe(fb);
  });

  it("documentShell projection: same topology → equal node/edge ids; shells omit live", () => {
    const a = projectShell(minimalExec("a"));
    const b = projectShell(minimalExec("a".repeat(200)));
    expect(a.nodes.map((n) => n.id)).toEqual(b.nodes.map((n) => n.id));
    expect(a.edges.map((e) => e.id)).toEqual(b.edges.map((e) => e.id));
    // Document shell still emits CEO sink even when captainStatus is null
    // (Live fills status); regression: gating on captainStatus dropped the node.
    expect(
      a.nodes.some((n) => n.type === "captain" && n.id === "captain"),
    ).toBe(true);
    const captain = a.nodes.find((n) => n.id === "captain");
    expect(captain?.data).toMatchObject({
      variant: "captain",
      runId: "captain",
    });
    expect(captain?.data).not.toHaveProperty("status");
    expect(captain?.data).not.toHaveProperty("preview");
    const worker = a.nodes.find((n) => n.id === "w1");
    expect(worker?.data).not.toHaveProperty("outputPreview");
    expect(worker?.data).not.toHaveProperty("status");
    expect(worker?.data).not.toHaveProperty("onActivate");
    expect(worker?.data).toMatchObject({ runId: "w1" });
    for (const e of a.edges) {
      expect(e.data).not.toHaveProperty("animated");
      expect(e.animated).toBeUndefined();
    }
  });

  it("shell snapshot key ignores streaming content (positions-only)", () => {
    const key = graphShellSnapshotKey({
      positions,
      groups: [],
      nodeSizes: {},
      actCards: [],
      bbox: { width: 1, height: 1 },
      edgeIds: ["e1"],
    });
    const key2 = graphShellSnapshotKey({
      positions,
      groups: [],
      nodeSizes: {},
      actCards: [],
      bbox: { width: 1, height: 1 },
      edgeIds: ["e1"],
    });
    expect(key).toBe(key2);
  });
});
