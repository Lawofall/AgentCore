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

const MID = "msg-background-strip";

const plan: ExecutionPlan = {
  id: "exec-bg",
  planType: "multi_agent",
  taskSummary: "后台调研",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "w2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r2", agentId: "w2", task: "撰写", dependsOn: [] },
  ],
};

const runningFrames: RunFrame[] = [
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
    outputSummary: "调研完成",
    durationMs: 100,
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
            onReplay={() => {}}
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

describe("StatusStrip · execution_detached 后台运行", () => {
  it("「后台」徽标 + 活体 n/m + 转圈（不冻 stamp 快照）", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setExecutionDetached(
      {
        execution_id: "exec-bg",
        conversation_id: "c1",
        completed: 0,
        total: 2,
        host_turn_id: MID,
      },
      MID,
    );
    const exec = projectExecution(plan, runningFrames, "running");
    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-background")).toBeTruthy();
    expect(
      screen.getByTestId("status-strip-background-title").textContent,
    ).toBe("后台");
    expect(screen.queryByText("团队后台运行中")).toBeNull();
    // Live frames: r1 completed, r2 running → 1/2, not frozen detached 0/2.
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.queryByText("0/2")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });

  it("detached 后不显示冻结的 coordinationWait n/m（含迟到心跳）", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setCoordinationWait(
      {
        execution_id: "exec-bg",
        waiting: true,
        completed: 0,
        total: 4,
      },
      MID,
    );
    useExecutionStore.getState().setExecutionDetached(
      {
        execution_id: "exec-bg",
        conversation_id: "c1",
        completed: 0,
        total: 2,
        host_turn_id: MID,
      },
      MID,
    );
    // Late wait heartbeat (still allowed while streaming). Must not revive 0/4.
    useExecutionStore.getState().setCoordinationWait(
      {
        execution_id: "exec-bg",
        waiting: true,
        completed: 0,
        total: 4,
      },
      MID,
    );
    const exec = projectExecution(plan, runningFrames, "running");
    renderStrip(exec);

    expect(screen.getByTestId("status-strip-background")).toBeTruthy();
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.queryByText("0/4")).toBeNull();
    expect(screen.queryByTestId("status-strip-coordination-wait")).toBeNull();
  });

  it("未 stamp detached 时保持旧 hold 转圈（兼容）", () => {
    const exec = projectExecution(plan, runningFrames, "running");
    const { container } = renderStrip(exec);

    expect(screen.queryByTestId("status-strip-background")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });
});
