// @vitest-environment jsdom
/**
 * 状态条只留细工具栏：合成草稿 / 等待解说 / 成员摘要不寄居条上。
 * 成员细节走协作图节点；n/m 仍在工具栏。
 */
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

const MID = "msg-synth-preview";

const plan: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: "并行调研",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "w2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r2", agentId: "w2", task: "撰写", dependsOn: [] },
  ],
};

const bothWorkersDone: RunFrame[] = [
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
  {
    t: 4,
    kind: "run_completed",
    runId: "r2",
    agentId: "w2",
    outputSummary: "撰写完成",
    durationMs: 120,
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

describe("StatusStrip · 不挂合成草稿行", () => {
  it("preview 事件不把草稿/小结画进工具栏", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setTeamSynthesisPreview(
      {
        execution_id: "exec-1",
        completed: 1,
        total: 2,
        headline: "合成草稿更新 · 已完成 1/2",
        text: "两边方向一致：优先方案 A，撰写员按此定稿。",
        workers: [],
        in_progress: true,
      },
      MID,
    );

    renderStrip(projectExecution(plan, [], "running"));

    expect(screen.queryByTestId("team-synthesis-preview")).toBeNull();
    expect(screen.queryByText("进展中")).toBeNull();
    expect(screen.queryByText("合成草稿更新 · 已完成 1/2")).toBeNull();
    expect(screen.queryByText(/两边方向一致/)).toBeNull();
    expect(screen.queryByText("并行调研")).toBeNull();
    expect(screen.getByText("0/2")).toBeTruthy();
  });

  it("汇总空窗：工人全完成仍 running 时只报 n/m，无解说标题", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    const execution = projectExecution(plan, bothWorkersDone, "running");
    expect(execution.progress).toEqual({ completed: 2, total: 2 });

    renderStrip(execution);

    expect(screen.getByTestId("status-strip-synthesizing")).toBeTruthy();
    expect(screen.getByText("2/2")).toBeTruthy();
    expect(screen.queryByTestId("status-strip-running-title")).toBeNull();
    expect(screen.queryByText("2/2 已完成，正在收尾")).toBeNull();
    expect(screen.queryByTestId("team-synthesis-preview")).toBeNull();
    expect(screen.queryByText("生成汇总")).toBeNull();
  });

  it("execution.completed 但工人未齐时不画完成勾", () => {
    useExecutionStore.getState().startExecution(plan, MID);
    const oneStillRunning = bothWorkersDone.slice(0, 3);
    const execution = projectExecution(plan, oneStillRunning, "completed");
    expect(execution.runs.some((r) => r.status === "running")).toBe(true);

    renderStrip(execution);

    expect(screen.queryByLabelText("完成")).toBeNull();
  });
});
