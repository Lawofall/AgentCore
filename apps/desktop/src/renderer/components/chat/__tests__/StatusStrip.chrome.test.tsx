// @vitest-environment jsdom
/**
 * 协作图状态条只留战绩。有接续 run、非零 collab、resolved 开工卡时，
 * 条上不得再出现「接续 N 次」「互相把关」「预计 N 人开工」「同时开工省下」、
 * 「回放协作过程」。
 * 数据字段 / formatCollabSummary / teamPreviewLead 仍保留，只是不画在这条上。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { formatCollabSummary } from "@/lib/collabSummary";
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

const MID = "msg-strip-chrome";

const plan: ExecutionPlan = {
  id: "exec-strip-chrome",
  planType: "multi_agent",
  taskSummary: "调研定价",
  agents: [
    { id: "a1", role: "调研员" },
    { id: "a2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "a1", task: "调研竞品", dependsOn: [] },
    { id: "r2", agentId: "a2", task: "写建议", dependsOn: ["r1"] },
    { id: "r1v2", agentId: "a1", task: "按指示改调研", dependsOn: [] },
  ],
};

function started(
  runId: string,
  agentId: string,
  t: number,
  continuesRunId: string | null = null,
): RunFrame {
  return {
    t,
    kind: "run_started",
    runId,
    agentId,
    parentRunId: null,
    runKind: "agent",
    continuesRunId,
  };
}

function done(runId: string, agentId: string, t: number): RunFrame {
  return {
    t,
    kind: "run_completed",
    runId,
    agentId,
    outputSummary: "产出",
    durationMs: 1_000,
  };
}

const frames: RunFrame[] = [
  started("r1", "a1", 1_000),
  done("r1", "a1", 2_000),
  started("r2", "a2", 2_000),
  done("r2", "a2", 3_000),
  started("r1v2", "a1", 3_000, "r1"),
  done("r1v2", "a1", 4_000),
];

function renderStrip() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
    },
  });
  client.setQueryData(conversationKeys.grouped, {
    folders: [],
    conversations: [],
  });
  const execution = projectExecution(plan, frames, "completed");
  useExecutionStore.getState().startExecution(plan, MID);
  useExecutionStore.setState((s) => ({
    byId: { ...s.byId, [MID]: { ...s.byId[MID], frames } },
  }));
  return {
    execution,
    ...render(
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
    ),
  };
}

afterEach(cleanup);
beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("StatusStrip · 去掉多余 chrome", () => {
  it("有接续 run + 非零 collab + resolved preview 时，条上不出现接续 / 互相把关 / 预计 / 同时开工省下", () => {
    const collabLine = formatCollabSummary({
      boundary_yields: 0,
      scope_signals: 1,
      revises: 2,
      escalations: 0,
    });
    expect(collabLine).toBe("互相把关：发现跑偏 1 处 · 返工重写 2 处");

    const { execution } = renderStrip();
    expect(execution.runs.some((r) => r.continuesRunId != null)).toBe(true);

    expect(screen.getByText(/3\/3/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "在画布打开" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "回放协作过程" })).toBeNull();
    expect(screen.queryByText(/团队完成/)).toBeNull();
    expect(screen.queryByText(/接续/)).toBeNull();
    expect(screen.queryByText(/互相把关/)).toBeNull();
    expect(screen.queryByText(/预计/)).toBeNull();
    expect(screen.queryByText(/同时开工省下/)).toBeNull();
    expect(screen.queryByTestId("graph-team-preview")).toBeNull();
  });
});
