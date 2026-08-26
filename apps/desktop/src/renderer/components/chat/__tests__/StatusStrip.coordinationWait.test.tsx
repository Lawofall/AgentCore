// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys } from "@/lib/queryKeys";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  projectExecution,
  useExecutionStore,
} from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { StatusStrip } from "../StatusStrip";

const MID = "msg-coord-wait";

const plan: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: "协调等待：并行调研 + 撰写",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "w2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r2", agentId: "w2", task: "撰写", dependsOn: [] },
  ],
};

const oneDoneFrames: RunFrame[] = [
  {
    t: 1,
    kind: "run_started",
    runId: "r1",
    agentId: "w1",
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  },
  {
    t: 2,
    kind: "run_started",
    runId: "r2",
    agentId: "w2",
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  },
  {
    t: 3,
    kind: "run_completed",
    runId: "r1",
    agentId: "w1",
    outputSummary: "完成调研",
    durationMs: 1000,
  },
];

function renderStrip(execution: ReturnType<typeof projectExecution>) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
    },
  });
  client.setQueryData(conversationKeys.grouped, {
    folders: [],
    conversations: [],
  });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ExecutionScopeContext.Provider value={MID}>
          <StatusStrip
            execution={execution}
            expanded
            onToggle={() => {}}
            onMaximize={() => {}}
          />
        </ExecutionScopeContext.Provider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("StatusStrip · coordination_wait", () => {
  it("wait shows n/m only (no talking title, no inline member panel)", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setCoordinationWait(
      {
        execution_id: "exec-1",
        waiting: true,
        completed: 1,
        total: 2,
      },
      MID,
    );

    const execution = projectExecution(plan, oneDoneFrames, "running");
    renderStrip(execution);

    // 工具栏只报 n/m；成员细节只靠图上 worker 节点。
    expect(screen.getByTestId("status-strip-coordination-wait")).toBeTruthy();
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.queryByText(/等待团队成员完成/)).toBeNull();
    expect(screen.queryByTestId("status-strip-running-title")).toBeNull();

    // 协调等待分支整体移除：不再内联渲染成员状态列表 / 协调等待徽标 / 重复 headline，
    // 成员级细节改由协作图节点承担。
    expect(screen.queryByTestId("team-synthesis-preview")).toBeNull();
    expect(screen.queryByText("协调等待")).toBeNull();
    expect(screen.queryByTestId("coordination-wait-workers")).toBeNull();
    expect(screen.queryByText("撰写员")).toBeNull();
    expect(screen.queryByText("研究员")).toBeNull();
  });

  it("heartbeats update completed/total; waiting=false clears", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setCoordinationWait(
      {
        execution_id: "exec-1",
        waiting: true,
        completed: 0,
        total: 2,
      },
      MID,
    );
    expect(useExecutionStore.getState().byId[MID]?.coordinationWait).toEqual({
      execution_id: "exec-1",
      waiting: true,
      completed: 0,
      total: 2,
    });

    useExecutionStore.getState().setCoordinationWait(
      {
        execution_id: "exec-1",
        waiting: true,
        completed: 1,
        total: 2,
      },
      MID,
    );
    expect(
      useExecutionStore.getState().byId[MID]?.coordinationWait?.completed,
    ).toBe(1);

    useExecutionStore.getState().setCoordinationWait(
      {
        execution_id: "exec-1",
        waiting: false,
        completed: 2,
        total: 2,
      },
      MID,
    );
    expect(useExecutionStore.getState().byId[MID]?.coordinationWait).toBeNull();
  });
});
