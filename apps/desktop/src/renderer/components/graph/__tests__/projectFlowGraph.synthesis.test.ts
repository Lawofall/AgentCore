import type { Execution } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { INPUT_ID } from "../constants";
import { projectFlowNodes } from "../projectFlowGraph";
import { buildGraphScene } from "../scene";

function minimalExec(): Execution {
  return {
    id: "e1",
    planType: "multi_agent",
    taskSummary: "并行调研",
    status: "running",
    agents: [],
    runs: [
      {
        id: "captain",
        agentId: "ceo",
        task: "",
        status: "pending",
        dependsOn: [],
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
        status: "completed",
        dependsOn: [],
        outputSummary: "ok",
        outputFiles: [],
        debrief: null,
        durationMs: 100,
        startedAt: null,
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
    progress: { completed: 1, total: 2 },
    acts: [],
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
  };
}

describe("projectFlowNodes · captain synthesis preview", () => {
  it("挂 team_synthesis_preview 片段到 running CEO 节点（无终稿时）", () => {
    const execution = minimalExec();
    const nodes = projectFlowNodes({
      execution,
      positions: {
        [INPUT_ID]: { x: 0, y: 0 },
        captain: { x: 0, y: 200 },
        w1: { x: 0, y: 100 },
      },
      nodeSizes: {},
      handleDirection: "vertical",
      litRunId: null,
      litEndpointMessageId: null,
      captainRun: { id: "captain" },
      captainStatus: "running",
      finalAnswer: null,
      captainSynthesisPreview: "两边方向一致：优先方案 A。",
      taskMessage: null,
      activateNode: () => {},
      groups: [],
      scene: buildGraphScene(execution),
    });

    const captain = nodes.find((n) => n.id === "captain");
    expect(captain?.data).toMatchObject({
      variant: "captain",
      status: "running",
      preview: "两边方向一致：优先方案 A。",
    });
  });

  it("终稿优先于 synthesis preview", () => {
    const execution = minimalExec();
    const nodes = projectFlowNodes({
      execution,
      positions: {
        [INPUT_ID]: { x: 0, y: 0 },
        captain: { x: 0, y: 200 },
        w1: { x: 0, y: 100 },
      },
      nodeSizes: {},
      handleDirection: "vertical",
      litRunId: null,
      litEndpointMessageId: null,
      captainRun: { id: "captain" },
      captainStatus: "running",
      finalAnswer: { id: "ans", content: "最终方案全文在此。" },
      captainSynthesisPreview: "草稿不应出现",
      taskMessage: null,
      activateNode: () => {},
      groups: [],
      scene: buildGraphScene(execution),
    });

    const captain = nodes.find((n) => n.id === "captain");
    expect(captain?.data.preview).toContain("最终方案");
    expect(String(captain?.data.preview)).not.toContain("草稿不应出现");
  });

  it("coordination wait: pending captain gets running chrome + caption", () => {
    const execution = minimalExec();
    // Worker still in flight → deriveCaptainStatus would be pending.
    execution.runs.push({
      id: "w2",
      agentId: "w2",
      task: "撰写",
      status: "running",
      dependsOn: [],
      outputSummary: null,
      outputFiles: [],
      debrief: null,
      durationMs: null,
      startedAt: null,
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
    });
    const nodes = projectFlowNodes({
      execution,
      positions: {
        [INPUT_ID]: { x: 0, y: 0 },
        captain: { x: 0, y: 200 },
        w1: { x: 0, y: 100 },
        w2: { x: 100, y: 100 },
      },
      nodeSizes: {},
      handleDirection: "vertical",
      litRunId: null,
      litEndpointMessageId: null,
      captainRun: { id: "captain" },
      captainStatus: "pending",
      finalAnswer: null,
      captainSynthesisPreview: "草稿不应盖过等待文案",
      captainStatusCaption: "等待「撰写员」(1/2)",
      taskMessage: null,
      activateNode: () => {},
      groups: [],
      scene: buildGraphScene(execution),
    });

    const captain = nodes.find((n) => n.id === "captain");
    const waitCaption = "等待「撰写员」(1/2)";
    expect(captain?.data).toMatchObject({
      variant: "captain",
      status: "running",
      statusCaption: waitCaption,
    });
    // Pure wait: preview must stay empty (or at least not echo waitCaption).
    expect(captain?.data.preview ?? "").toBe("");
    expect(String(captain?.data.preview ?? "")).not.toBe(waitCaption);
  });

  it("待汇总不把派单等待句摘上 CEO 格子", () => {
    const execution = minimalExec();
    execution.runs.push({
      id: "w2",
      agentId: "w2",
      task: "撰写",
      status: "running",
      dependsOn: [],
      outputSummary: null,
      outputFiles: [],
      debrief: null,
      durationMs: null,
      startedAt: null,
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
    });
    const nodes = projectFlowNodes({
      execution,
      positions: {
        [INPUT_ID]: { x: 0, y: 0 },
        captain: { x: 0, y: 200 },
        w1: { x: 0, y: 100 },
        w2: { x: 100, y: 100 },
      },
      nodeSizes: {},
      handleDirection: "vertical",
      litRunId: null,
      litEndpointMessageId: null,
      captainRun: { id: "captain" },
      captainStatus: "pending",
      finalAnswer: {
        id: "ans",
        content: "人已派出，验证员还在复核，你先忙别的。",
      },
      captainSynthesisPreview: "",
      taskMessage: null,
      activateNode: () => {},
      groups: [],
      scene: buildGraphScene(execution),
    });

    const captain = nodes.find((n) => n.id === "captain");
    expect(captain?.data.status).toBe("pending");
    expect(captain?.data.preview ?? "").toBe("");
    expect(String(captain?.data.preview ?? "")).not.toContain("人已派出");
  });
});
