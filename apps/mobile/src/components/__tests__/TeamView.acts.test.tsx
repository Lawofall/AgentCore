// @vitest-environment jsdom
/**
 * 批 A4：多幕团队列表分组头——手机列表语言（非桌面幕分带）。
 */
import { TeamView } from "@/components/TeamView";
import type {
  ProjectedAct,
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

function makeRun(
  p: Partial<ProjectedRun> & { id: string; actId: string },
): ProjectedRun {
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
    ...p,
  };
}

describe("TeamView · 多幕分组", () => {
  it("acts≥2 时按幕插入分组头，头部显示幕数", () => {
    const acts: ProjectedAct[] = [
      {
        actId: "act-1",
        kind: "multi_agent",
        title: "多视角调研",
        anchorRunId: null,
        authorizedBy: null,
      },
      {
        actId: "act-2",
        kind: "debate",
        title: "辩论对抗",
        anchorRunId: "syn",
        authorizedBy: "stage_card",
      },
    ];
    const agents = [
      makeAgent({ id: "l1", role: "透镜" }),
      makeAgent({ id: "mod", role: "主持人" }),
    ];
    const runs = [
      makeRun({
        id: "r-lens",
        agentId: "l1",
        role: "透镜",
        actId: "act-1",
      }),
      makeRun({
        id: "r-mod",
        agentId: "mod",
        role: "主持人",
        actId: "act-2",
        stance: "pro",
      }),
    ];
    render(
      <TeamView
        agents={agents}
        runs={runs}
        progress={{ completed: 2, total: 2 }}
        acts={acts}
      />,
    );
    expect(screen.getByText("2 幕")).toBeTruthy();
    // 多幕时不扁平标「辩论」
    expect(screen.queryByText("辩论", { selector: ".team-tag" })).toBeNull();
    expect(screen.getByText("多视角调研")).toBeTruthy();
    expect(screen.getByText("辩论对抗")).toBeTruthy();
    expect(screen.getByText("经推进卡授权")).toBeTruthy();
  });

  it("证人席位 pending 显示待命、skipped 显示未传唤", () => {
    render(
      <TeamView
        agents={[makeAgent({ id: "wit", role: "证人·法律", status: "idle" })]}
        runs={[
          makeRun({
            id: "seat",
            agentId: "wit",
            role: "证人·法律",
            actId: "act-1",
            status: "pending",
            group: "debate:witness",
            continuesRunId: null,
          }),
        ]}
        progress={{ completed: 0, total: 1 }}
      />,
    );
    expect(screen.getByText("待命")).toBeTruthy();
    cleanup();
    render(
      <TeamView
        agents={[makeAgent({ id: "wit", role: "证人·法律", status: "idle" })]}
        runs={[
          makeRun({
            id: "seat",
            agentId: "wit",
            role: "证人·法律",
            actId: "act-1",
            status: "skipped",
            group: "debate:witness",
            continuesRunId: null,
          }),
        ]}
        progress={{ completed: 0, total: 1 }}
      />,
    );
    expect(screen.getByText("未传唤")).toBeTruthy();
  });

  it("run.phase 徽章区分思考/工具/等子/收尾；pending=排队中；skipped=未执行", () => {
    const agents = [makeAgent({ id: "w1", role: "队员", status: "working" })];
    const cases: Array<{
      phase?: ProjectedRun["phase"];
      status: ProjectedRun["status"];
      label: string;
    }> = [
      { status: "running", phase: "thinking", label: "思考中" },
      { status: "running", phase: "tool", label: "工具中" },
      { status: "running", phase: "waiting_children", label: "等待子任务" },
      { status: "running", phase: "winding_down", label: "收尾中" },
      { status: "pending", label: "排队中" },
      { status: "skipped", label: "未执行" },
    ];
    for (const c of cases) {
      cleanup();
      render(
        <TeamView
          agents={agents}
          runs={[
            makeRun({
              id: "r1",
              agentId: "w1",
              role: "队员",
              actId: "act-1",
              status: c.status,
              phase: c.phase,
            }),
          ]}
          progress={{
            completed: c.status === "skipped" ? 0 : 0,
            total: 1,
          }}
        />,
      );
      expect(screen.getByText(c.label)).toBeTruthy();
    }
  });

  it("单幕辩论仍显示头部「辩论」徽标、无分组头", () => {
    const acts: ProjectedAct[] = [
      {
        actId: "act-1",
        kind: "debate",
        title: "辩论对抗",
        anchorRunId: null,
        authorizedBy: null,
      },
    ];
    render(
      <TeamView
        agents={[makeAgent({ id: "mod", role: "主持人" })]}
        runs={[
          makeRun({
            id: "r1",
            agentId: "mod",
            role: "主持人",
            actId: "act-1",
            stance: "pro",
          }),
        ]}
        progress={{ completed: 1, total: 1 }}
        acts={acts}
      />,
    );
    expect(screen.getByText("辩论", { selector: ".team-tag" })).toBeTruthy();
    expect(
      screen.queryByText("辩论对抗", { selector: ".team-act-title" }),
    ).toBeNull();
  });
});
