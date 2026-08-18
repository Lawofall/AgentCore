// @vitest-environment jsdom
/**
 * 团队条对齐桌面工具栏：图标 + n/m，不画「生成汇总 / 团队进展」长行。
 * 协调等待只盖数字；摘要留在队员卡。
 */
import { TeamView } from "@/components/TeamView";
import type {
  ProjectedAgent,
  ProjectedRun,
} from "@agentcore/protocol-conformance";
import { cleanup, render, screen } from "@testing-library/react";
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

describe("TeamView 团队条 · 进度与合成行", () => {
  it("进行中有队员摘要时不画合成预览行，摘要留在队员卡", () => {
    render(
      <TeamView
        agents={[
          makeAgent({ id: "a1", role: "工程实践研究员" }),
          makeAgent({ id: "a2", role: "学术视角研究员", status: "working" }),
        ]}
        runs={[
          makeRun({
            id: "r1",
            agentId: "a1",
            role: "工程实践研究员",
            status: "completed",
            outputSummary: "工程实践结论",
          }),
          makeRun({
            id: "r2",
            agentId: "a2",
            role: "学术视角研究员",
            status: "running",
          }),
        ]}
        progress={{ completed: 1, total: 2 }}
        status="running"
      />,
    );
    expect(screen.queryByTestId("team-synthesis-preview")).toBeNull();
    expect(screen.queryByText("生成汇总")).toBeNull();
    expect(screen.queryByText("团队进展")).toBeNull();
    expect(screen.getByText("产出预览")).toBeTruthy();
    expect(screen.getByText("工程实践结论")).toBeTruthy();
    expect(screen.getByText("1/2")).toBeTruthy();
  });

  it("coordination_wait 只盖条上 n/m，不写长句", () => {
    render(
      <TeamView
        agents={[
          makeAgent({ id: "a1", role: "调研员" }),
          makeAgent({ id: "a2", role: "分析师", status: "working" }),
        ]}
        runs={[
          makeRun({ id: "r1", agentId: "a1", role: "调研员" }),
          makeRun({
            id: "r2",
            agentId: "a2",
            role: "分析师",
            status: "running",
          }),
        ]}
        progress={{ completed: 0, total: 2 }}
        waitProgress={{ completed: 1, total: 2 }}
        status="running"
      />,
    );
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.getByText("1/2 子任务")).toBeTruthy();
    expect(screen.queryByText("0/2 子任务")).toBeNull();
    expect(screen.queryByText(/等待团队成员完成/)).toBeNull();
  });

  it("execution_detached 挂「后台」，CEO 已收口仍当进行中", () => {
    render(
      <TeamView
        agents={[
          makeAgent({ id: "a1", role: "调研员" }),
          makeAgent({ id: "a2", role: "分析师", status: "working" }),
        ]}
        runs={[
          makeRun({ id: "r1", agentId: "a1", role: "调研员" }),
          makeRun({
            id: "r2",
            agentId: "a2",
            role: "分析师",
            status: "running",
          }),
        ]}
        progress={{ completed: 1, total: 2 }}
        status="completed"
        detached
        elapsedMs={12_000}
      />,
    );
    expect(screen.getByTestId("team-strip-background").textContent).toBe(
      "后台",
    );
    expect(document.querySelector(".team-strip-mark.mark-run")).toBeTruthy();
    expect(screen.queryByText(/用时/)).toBeNull();
  });

  it("hydrate 后无 detached 事件、队员仍在跑 → 仍挂后台", () => {
    render(
      <TeamView
        agents={[
          makeAgent({ id: "a1", role: "调研员" }),
          makeAgent({ id: "a2", role: "分析师", status: "working" }),
        ]}
        runs={[
          makeRun({ id: "r1", agentId: "a1", role: "调研员" }),
          makeRun({
            id: "r2",
            agentId: "a2",
            role: "分析师",
            status: "running",
          }),
        ]}
        progress={{ completed: 1, total: 2 }}
        status="completed"
      />,
    );
    expect(screen.getByTestId("team-strip-background")).toBeTruthy();
  });

  it("全员收口不挂后台", () => {
    render(
      <TeamView
        agents={[makeAgent({ id: "a1", role: "调研员" })]}
        runs={[makeRun({ id: "r1", agentId: "a1", role: "调研员" })]}
        progress={{ completed: 1, total: 1 }}
        status="completed"
      />,
    );
    expect(screen.queryByTestId("team-strip-background")).toBeNull();
  });
});
