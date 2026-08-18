// @vitest-environment jsdom
/**
 * 团队条「用时」= 回合墙钟跨度。
 *
 * 回归钉：曾按队员时长求和，同一回合桌面「用时 40s」、手机「用时 2m10s」——并行度越高手机
 * 的数字越大。用时只认墙钟，不回潮成队员工时之和。
 */
import { TeamView } from "@/components/TeamView";
import type {
  ProjectedAgent,
  ProjectedRun,
} from "@agentcore/protocol-conformance";
import { turnElapsedMs } from "@agentcore/protocol-fold-kit";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

function makeAgent(
  p: Partial<ProjectedAgent> & { id: string; role: string },
): ProjectedAgent {
  return {
    thinking: false,
    status: "completed",
    currentRunId: null,
    output: "",
    reasoning: "",
    toolProgress: null,
    ...p,
  };
}

function makeRun(p: Partial<ProjectedRun> & { id: string }): ProjectedRun {
  return {
    agentId: "a1",
    task: "task",
    status: "completed",
    dependsOn: [],
    outputSummary: null,
    debrief: null,
    durationMs: null,
    error: null,
    failureKind: null,
    productLanded: null,
    parentRunId: null,
    kind: "agent",
    role: "队员",
    model: null,
    usage: null,
    cost: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    revised: null,
    replacesRunId: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    actId: "act-1",
    ...p,
  };
}

/** 三名队员各跑约 40s，但同时开跑：用户等了 42s。 */
const PARALLEL_TURN = {
  agents: [
    makeAgent({ id: "a1", role: "调研员" }),
    makeAgent({ id: "a2", role: "分析师" }),
    makeAgent({ id: "a3", role: "审校" }),
  ],
  runs: [
    makeRun({ id: "r1", agentId: "a1", role: "调研员", durationMs: 39_000 }),
    makeRun({ id: "r2", agentId: "a2", role: "分析师", durationMs: 40_000 }),
    makeRun({ id: "r3", agentId: "a3", role: "审校", durationMs: 42_000 }),
  ],
  progress: { completed: 3, total: 3 },
};

function stripText(): string {
  return document.querySelector(".team-strip-meta")?.textContent ?? "";
}

describe("TeamView 团队条 · 用时", () => {
  it("显示回合墙钟跨度，不随队员数累加", () => {
    render(
      <TeamView
        agents={PARALLEL_TURN.agents}
        runs={PARALLEL_TURN.runs}
        progress={PARALLEL_TURN.progress}
        status="completed"
        elapsedMs={42_000}
      />,
    );
    expect(stripText()).toContain("用时 42s");
    // 队员时长求和是 2m1s——绝不能是它。
    expect(stripText()).not.toContain("2m1s");
  });

  it("跨度来自协作事实流首末事件（与桌面 elapsedMs(frames) 同源）", () => {
    const at = (sec: number) =>
      new Date(Date.UTC(2026, 0, 1, 0, 0, sec)).toISOString();
    const events = [
      { type: "message_start", timestamp: at(0) },
      { type: "run_started", timestamp: at(2) },
      { type: "run_started", timestamp: at(2) },
      { type: "run_started", timestamp: at(2) },
      { type: "run_completed", timestamp: at(41) },
      { type: "run_completed", timestamp: at(42) },
      { type: "run_completed", timestamp: at(44) },
      { type: "content_delta", timestamp: at(60) },
    ];
    render(
      <TeamView
        agents={PARALLEL_TURN.agents}
        runs={PARALLEL_TURN.runs}
        progress={PARALLEL_TURN.progress}
        status="completed"
        elapsedMs={turnElapsedMs(events)}
      />,
    );
    expect(stripText()).toContain("用时 42s");
  });

  it("进行中不显示用时；无跨度（0）也不显示", () => {
    render(
      <TeamView
        agents={PARALLEL_TURN.agents}
        runs={PARALLEL_TURN.runs}
        progress={{ completed: 1, total: 3 }}
        status="running"
        elapsedMs={42_000}
      />,
    );
    expect(stripText()).not.toContain("用时");
    cleanup();
    render(
      <TeamView
        agents={PARALLEL_TURN.agents}
        runs={PARALLEL_TURN.runs}
        progress={PARALLEL_TURN.progress}
        status="completed"
        elapsedMs={0}
      />,
    );
    expect(stripText()).not.toContain("用时");
  });

  it("队员卡片脚仍显示这一个人的耗时（单人耗时 ≠ 回合用时）", () => {
    render(
      <TeamView
        agents={PARALLEL_TURN.agents}
        runs={PARALLEL_TURN.runs}
        progress={PARALLEL_TURN.progress}
        status="completed"
        elapsedMs={42_000}
      />,
    );
    expect(screen.getByText(/39\.0s/)).toBeTruthy();
  });

  it("默认展开队员列表，仍可收起", () => {
    render(
      <TeamView
        agents={PARALLEL_TURN.agents}
        runs={PARALLEL_TURN.runs}
        progress={PARALLEL_TURN.progress}
        status="completed"
        elapsedMs={42_000}
      />,
    );
    expect(screen.getByRole("button", { name: "收起协作列表" })).toBeTruthy();
    expect(screen.getByText("调研员")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "收起协作列表" }));
    expect(screen.queryByText("调研员")).toBeNull();
    expect(screen.getByRole("button", { name: "展开协作列表" })).toBeTruthy();
  });
});
