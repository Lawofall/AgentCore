// @vitest-environment jsdom
/**
 * 主持人 pending 空窗分流：
 * - 开质询 + 立论完 + 尚无质询问答 → 「主持人正在拟质询…」
 * - 质询作答完成后 → 「正在小结…」
 * - 未开质询（快速对碰）→ 「正在小结…」
 */

import type { Execution, RunNode } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type {
  DebateCrossExamView,
  DebateModel,
  DebateRoundModel,
  DebateSideModel,
} from "../../model";
import { Transcript } from "../Transcript";

afterEach(() => {
  cleanup();
});

function side(
  key: string,
  status: RunNode["status"] = "completed",
): DebateSideModel {
  return {
    key: `run_${key}`,
    sideKey: key,
    name: key === "pro" ? "支持方" : "反对方",
    stance: key === "pro" ? "pro" : "con",
    colorVar: "var(--debate-side-pro)",
    model: "",
    run: {
      id: `run_${key}`,
      status,
    } as unknown as RunNode,
  };
}

function round(overrides: Partial<DebateRoundModel> = {}): DebateRoundModel {
  return {
    roundNo: 1,
    focus: "收益与风险",
    summary: "",
    verdict: null,
    sides: [side("pro"), side("con")],
    clashes: [],
    inFlight: true,
    userInterjections: [],
    crossExam: [],
    witnessExam: [],
    scores: [],
    findings: [],
    threadTurns: [],
    ...overrides,
  };
}

function model(overrides: Partial<DebateModel> = {}): DebateModel {
  return {
    form: "debate",
    motion: null,
    stopReason: null,
    moderatorRunId: null,
    moderatorModel: null,
    moderatorOrigin: null,
    sameModelDebate: false,
    narrativeFirst: false,
    rounds: [round()],
    brief: null,
    sides: null,
    closings: [],
    opening: null,
    settled: false,
    crossExamEnabled: false,
    evidenceLedger: [],
    subtopics: null,
    ...overrides,
  };
}

const emptyExec = {
  runs: [],
  agents: [],
} as unknown as Execution;

describe("Transcript moderator pending", () => {
  it("开质询 + 立论完 + 尚无质询问答 → 拟质询文案", () => {
    render(
      <Transcript
        model={model({
          crossExamEnabled: true,
          rounds: [round({ crossExam: [] })],
        })}
        execution={emptyExec}
        messageId="m1"
      />,
    );
    expect(screen.getByText("主持人正在拟质询…")).toBeTruthy();
    expect(screen.queryByText("正在小结…")).toBeNull();
  });

  it("质询作答完成后 → 小结文案", () => {
    const cx: DebateCrossExamView = {
      targetKey: "pro",
      stance: "pro",
      targetName: "支持方",
      targetColorVar: "var(--debate-side-pro)",
      exchanges: [{ question: "Q?", answer: "A" }],
      answerRun: {
        id: "cx_pro",
        status: "completed",
      } as unknown as RunNode,
    };
    render(
      <Transcript
        model={model({
          crossExamEnabled: true,
          rounds: [round({ crossExam: [cx] })],
        })}
        execution={emptyExec}
        messageId="m1"
      />,
    );
    expect(screen.getByText("正在小结…")).toBeTruthy();
    expect(screen.queryByText("主持人正在拟质询…")).toBeNull();
  });

  it("快速对碰（未开质询）→ 小结文案", () => {
    render(
      <Transcript
        model={model({
          crossExamEnabled: false,
          rounds: [round({ crossExam: [] })],
        })}
        execution={emptyExec}
        messageId="m1"
      />,
    );
    expect(screen.getByText("正在小结…")).toBeTruthy();
    expect(screen.queryByText("主持人正在拟质询…")).toBeNull();
  });
});
