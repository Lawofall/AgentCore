import type { Execution, RunNode } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { captainSinkPreview, deriveCaptainStatus } from "../helpers";

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

describe("deriveCaptainStatus", () => {
  it("returns running when workers are done and execution still running", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap")).toBe("running");
  });

  it("returns pending when execution is paused even if all workers done", () => {
    const e = exec({
      status: "paused",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    // Clears「正在收尾」sink; RunStatus has no paused.
    expect(deriveCaptainStatus(e, "cap")).toBe("pending");
  });

  it("stays pending when CEO turn ended but a worker is still live", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "running" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap", { turnTerminal: true })).toBe(
      "pending",
    );
  });

  it("does not trust execution.completed while a worker is still live", () => {
    const e = exec({
      status: "completed",
      runs: [
        run({ id: "cap", status: "completed", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "running" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap", { turnTerminal: true })).toBe(
      "pending",
    );
  });

  it("stays running after CEO turn ended while still attached (same-turn close)", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap", { turnTerminal: true })).toBe(
      "running",
    );
  });

  it("does not paint synthesizing sink after captain detached", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(
      deriveCaptainStatus(e, "cap", { turnTerminal: true, detached: true }),
    ).toBe("pending");
  });

  it("returns completed when execution completed and all workers terminal", () => {
    const e = exec({
      status: "completed",
      runs: [
        run({ id: "cap", status: "completed", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        run({ id: "w2", status: "completed" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap", { turnTerminal: true })).toBe(
      "completed",
    );
  });

  it("returns completed for a captain-only turn that already ended", () => {
    const e = exec({
      status: "running",
      runs: [run({ id: "cap", status: "pending", kind: "captain" })],
    });
    expect(deriveCaptainStatus(e, "cap", { turnTerminal: true })).toBe(
      "completed",
    );
  });

  it("ignores extra append-turn captains when judging worker completion", () => {
    const e = exec({
      status: "running",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
        // Leaked append captain still pending — must not block sink「汇总中」.
        run({ id: "cap2", status: "pending", kind: "captain" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap")).toBe("running");
  });

  it("does not paint captain failed from whole-graph execution.failed", () => {
    const e = exec({
      status: "failed",
      runs: [
        run({ id: "cap", status: "pending", kind: "captain" }),
        run({ id: "w1", status: "failed" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap")).toBe("pending");
    expect(deriveCaptainStatus(e, "cap")).not.toBe("failed");
  });

  it("paused execution does not redden CEO 汇总 even if captain run failed", () => {
    const e = exec({
      status: "paused",
      runs: [
        run({ id: "cap", status: "failed", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap")).toBe("pending");
    expect(deriveCaptainStatus(e, "cap")).not.toBe("failed");
  });

  it("paints captain failed only when the captain run itself failed", () => {
    const e = exec({
      status: "failed",
      runs: [
        run({ id: "cap", status: "failed", kind: "captain" }),
        run({ id: "w1", status: "completed" }),
      ],
    });
    expect(deriveCaptainStatus(e, "cap")).toBe("failed");
  });
});

describe("captainSinkPreview", () => {
  it("待汇总不摘派单等待句", () => {
    expect(
      captainSinkPreview({
        captainStatus: "pending",
        answerPreview: "人已派出，验证员还在复核，你先忙别的。",
        synthesisPreview: "",
      }),
    ).toBe("");
  });

  it("待汇总且无等待条时显示中间草稿", () => {
    expect(
      captainSinkPreview({
        captainStatus: "pending",
        answerPreview: "人已派出",
        synthesisPreview: "两边方向一致：优先方案 A。",
      }),
    ).toBe("两边方向一致：优先方案 A。");
  });

  it("待汇总有等待条时预览留空（不重复、不摘派单句）", () => {
    expect(
      captainSinkPreview({
        captainStatus: "pending",
        answerPreview: "人已派出，还在等。",
        synthesisPreview: "草稿不应盖过等待文案",
        waitCaption: "等待「撰写员」(1/2)",
      }),
    ).toBe("");
  });

  it("人齐后仍用派单泡开头", () => {
    expect(
      captainSinkPreview({
        captainStatus: "completed",
        answerPreview: "人已派出",
        synthesisPreview: "草稿不应出现",
      }),
    ).toBe("人已派出");
  });
});
