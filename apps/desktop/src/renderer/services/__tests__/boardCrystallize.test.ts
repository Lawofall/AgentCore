import {
  artifactFromRun,
  buildCrystallizedElements,
  crystallizedRunIds,
  primaryOutputFile,
} from "@/services/boardCrystallize";
import type { OverlayAnchor } from "@/services/boardProgress";
import type {
  AgentState,
  Execution,
  RunNode,
  RunStatus,
} from "@/stores/execution";
import { describe, expect, it } from "vitest";

const agent = (id: string, role: string): AgentState => ({
  id,
  role,
  thinking: true,
  status: "idle",
  currentRunId: null,
  outputChunks: [],
  reasoningChunks: [],
  toolCalls: [],
  toolProgress: null,
  toolExecutionLive: null,
});

const run = (
  id: string,
  agentId: string,
  status: RunStatus,
  over: Partial<RunNode> = {},
): RunNode => ({
  id,
  agentId,
  task: `task ${id}`,
  status,
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
  usage: null,
  cost: null,
  stance: null,
  group: null,
  round: 0,
  continuesRunId: null,
  continuationIndex: 0,
  revised: null,
  replacesRunId: null,
  checkpoint: null,
  receivedContext: [],
  escalations: [],
  process: [],
  ...over,
  sideKey: over.sideKey ?? null,
});

const execution = (over: Partial<Execution>): Execution => ({
  id: "exec1",
  planType: "multi_agent",
  taskSummary: "做点东西",
  status: "completed",
  agents: [],
  runs: [],
  acts: [],
  progress: { completed: 0, total: 0 },
  batches: [],
  debate: null,
  debateRounds: [],
  crossExamEnabled: false,
  debateOpening: null,
  debatePretrial: null,
  ...over,
});

const anchor: OverlayAnchor = { x: 100, y: 50, width: 200, height: 120 };

describe("buildCrystallizedElements", () => {
  it("returns [] when no worker reached a crystallizable状态", () => {
    expect(
      buildCrystallizedElements(
        execution({
          agents: [agent("ag1", "工程师")],
          runs: [run("r1", "ag1", "running")],
        }),
        anchor,
        new Set(),
      ),
    ).toEqual([]);
  });

  it("mints a node + product card + connectors for a completed worker", () => {
    const out = buildCrystallizedElements(
      execution({
        agents: [agent("ag1", "工程师")],
        runs: [run("r1", "ag1", "completed", { outputSummary: "做完了 X" })],
      }),
      anchor,
      new Set(),
    );
    const node = out.find((e) => e.id === "crys-node-r1");
    const art = out.find((e) => e.id === "crys-art-r1");
    const link = out.find((e) => e.id === "crys-link-r1");
    const src = out.find((e) => e.id === "crys-src-exec1");

    expect(node?.type).toBe("agentNode");
    expect(node?.runId).toBe("r1");
    expect(node?.role).toBe("工程师");
    expect(node?.runStatus).toBe("completed");

    expect(art?.type).toBe("artifactCard");
    expect(art?.runId).toBe("r1");
    expect(art?.artifactKind).toBe("text");
    expect(art?.title).toContain("工程师");
    expect(art?.text).toBe("做完了 X");

    // node → artifact connector is bound both ends so it reroutes when either card moves.
    expect(link?.type).toBe("arrow");
    expect(link?.start?.id).toBe("crys-node-r1");
    expect(link?.end?.id).toBe("crys-art-r1");

    // provenance arrow: node → brief's right edge (持久贴源).
    expect(src?.type).toBe("arrow");
    expect(src?.start?.id).toBe("crys-node-r1");
    expect(src?.points?.at(-1)?.[0]).toBe(anchor.x + anchor.width);
  });

  it("crystallizes a file artifact when outputFiles is present", () => {
    const out = buildCrystallizedElements(
      execution({
        agents: [agent("ag1", "工程师")],
        runs: [
          run("r1", "ag1", "completed", {
            outputSummary: "报告已写好",
            outputFiles: ["draft.md", "out/report.md"],
          }),
        ],
      }),
      anchor,
      new Set(),
    );
    const art = out.find((e) => e.id === "crys-art-r1");
    expect(art?.artifactKind).toBe("file");
    expect(art?.ref).toBe("out/report.md");
    expect(art?.title).toContain("report.md");
    expect(art?.text).toBe("报告已写好");
  });

  it("docks the team column to the right of the brief", () => {
    const out = buildCrystallizedElements(
      execution({
        agents: [agent("ag1", "工程师")],
        runs: [run("r1", "ag1", "completed", { outputSummary: "x" })],
      }),
      anchor,
      new Set(),
    );
    const rightEdge = anchor.x + anchor.width;
    for (const card of out.filter(
      (e) => e.type === "agentNode" || e.type === "artifactCard",
    )) {
      expect(card.x).toBeGreaterThan(rightEdge);
    }
  });

  it("keeps a failed worker as a node but mints no product card", () => {
    const out = buildCrystallizedElements(
      execution({
        status: "failed",
        agents: [agent("ag1", "工程师")],
        runs: [run("r1", "ag1", "failed", { error: "boom" })],
      }),
      anchor,
      new Set(),
    );
    expect(out.find((e) => e.id === "crys-node-r1")?.runStatus).toBe("failed");
    expect(out.find((e) => e.id === "crys-art-r1")).toBeUndefined();
  });

  it("mints a node but no product card for a completed worker without a summary", () => {
    const out = buildCrystallizedElements(
      execution({
        agents: [agent("ag1", "工程师")],
        runs: [run("r1", "ag1", "completed")],
      }),
      anchor,
      new Set(),
    );
    expect(out.find((e) => e.id === "crys-node-r1")).toBeDefined();
    expect(out.find((e) => e.id === "crys-art-r1")).toBeUndefined();
  });

  it("excludes the CEO captain and any non-terminal worker", () => {
    const out = buildCrystallizedElements(
      execution({
        agents: [
          agent("cap", "CEO"),
          agent("ag1", "工程师"),
          agent("ag2", "设计"),
        ],
        runs: [
          run("cap", "cap", "completed", {
            kind: "captain",
            outputSummary: "汇总",
          }),
          run("r1", "ag1", "completed", { outputSummary: "x" }),
          run("r2", "ag2", "running"),
        ],
      }),
      anchor,
      new Set(),
    );
    expect(out.find((e) => e.id === "crys-node-cap")).toBeUndefined();
    expect(out.find((e) => e.id === "crys-node-r2")).toBeUndefined();
    expect(out.find((e) => e.id === "crys-node-r1")).toBeDefined();
  });

  it("stacks multiple workers vertically", () => {
    const out = buildCrystallizedElements(
      execution({
        agents: [agent("ag1", "A"), agent("ag2", "B")],
        runs: [
          run("r1", "ag1", "completed", { outputSummary: "x" }),
          run("r2", "ag2", "completed", { outputSummary: "y" }),
        ],
      }),
      anchor,
      new Set(),
    );
    const n1 = out.find((e) => e.id === "crys-node-r1");
    const n2 = out.find((e) => e.id === "crys-node-r2");
    expect(n1).toBeDefined();
    expect(n2).toBeDefined();
    expect((n2 as { y: number }).y).toBeGreaterThan((n1 as { y: number }).y);
  });

  it("skips runs whose ids are already crystallized (idempotent)", () => {
    expect(
      buildCrystallizedElements(
        execution({
          agents: [agent("ag1", "工程师")],
          runs: [run("r1", "ag1", "completed", { outputSummary: "x" })],
        }),
        anchor,
        new Set(["r1"]),
      ),
    ).toEqual([]);
  });

  it("round-trips through crystallizedRunIds so a second pass dedupes", () => {
    const exec = execution({
      agents: [agent("ag1", "工程师")],
      runs: [run("r1", "ag1", "completed", { outputSummary: "x" })],
    });
    const first = buildCrystallizedElements(exec, anchor, new Set());
    const ids = crystallizedRunIds(first);
    expect(ids.has("r1")).toBe(true);
    expect(buildCrystallizedElements(exec, anchor, ids)).toEqual([]);
  });
});

describe("artifactFromRun / primaryOutputFile", () => {
  it("prefers file over text when outputFiles is non-empty", () => {
    expect(
      artifactFromRun({
        outputSummary: "摘要",
        outputFiles: ["a.md", "b.md"],
      }),
    ).toEqual({
      kind: "file",
      body: "摘要",
      titleSuffix: "b.md",
      ref: "b.md",
    });
  });

  it("falls back to text-only artifact", () => {
    expect(artifactFromRun({ outputSummary: "纯文本" })).toEqual({
      kind: "text",
      body: "纯文本",
      titleSuffix: "产物",
    });
  });

  it("primaryOutputFile picks the last path", () => {
    expect(primaryOutputFile(["x", "y/z.ts"])).toBe("y/z.ts");
  });
});
