// @vitest-environment jsdom
/**
 * 完成态状态条不再画「同时开工省下」。这组并行帧以前会算出 1m19s 省时，
 * 条上仍只留 n/m · 用时（花费本用例无价），不得出现该文案。
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

const MID = "msg-parallel-saving";

const plan: ExecutionPlan = {
  id: "exec-parallel-saving",
  planType: "multi_agent",
  taskSummary: "调研三个方向",
  agents: [
    { id: "a1", role: "调研员" },
    { id: "a2", role: "分析师" },
    { id: "a3", role: "审校" },
  ],
  runs: [
    { id: "r1", agentId: "a1", task: "方向一", dependsOn: [] },
    { id: "r2", agentId: "a2", task: "方向二", dependsOn: [] },
    { id: "r3", agentId: "a3", task: "方向三", dependsOn: [] },
  ],
};

function started(runId: string, agentId: string, t: number): RunFrame {
  return {
    t,
    kind: "run_started",
    runId,
    agentId,
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  };
}

function done(
  runId: string,
  agentId: string,
  t: number,
  durationMs: number,
): RunFrame {
  return {
    t,
    kind: "run_completed",
    runId,
    agentId,
    outputSummary: "产出",
    durationMs,
  };
}

/** 三人同时开跑：各 ~40s，回合跨度 42s（t 2s → 44s）。 */
const PARALLEL: RunFrame[] = [
  started("r1", "a1", 2_000),
  started("r2", "a2", 2_000),
  started("r3", "a3", 2_000),
  done("r1", "a1", 41_000, 39_000),
  done("r2", "a2", 42_000, 40_000),
  done("r3", "a3", 44_000, 42_000),
];

function renderStrip(frames: RunFrame[]) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
    },
  });
  client.setQueryData(conversationKeys.grouped, {
    folders: [],
    conversations: [],
  });
  useExecutionStore.getState().startExecution(plan, MID);
  useExecutionStore.setState((s) => ({
    byId: { ...s.byId, [MID]: { ...s.byId[MID], frames } },
  }));
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ExecutionScopeContext.Provider value={MID}>
          <StatusStrip
            execution={projectExecution(plan, frames, "completed")}
            expanded
            onToggle={() => {}}
            onMaximize={() => {}}
          />
        </ExecutionScopeContext.Provider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);
beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("StatusStrip · 完成态不再画并行省时", () => {
  it("并行回合：条上仍有 n/m · 用时，不得出现「同时开工省下」", () => {
    renderStrip(PARALLEL);
    expect(screen.getByText(/3\/3/)).toBeTruthy();
    expect(screen.getByText(/用时 42s/)).toBeTruthy();
    expect(screen.queryByText(/同时开工省下/)).toBeNull();
    expect(screen.queryByTestId("status-strip-parallel-saving")).toBeNull();
  });
});
