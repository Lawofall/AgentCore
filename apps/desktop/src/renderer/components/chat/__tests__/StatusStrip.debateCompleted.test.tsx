// @vitest-environment jsdom
/**
 * 辩论完成态 StatusStrip：meta（子任务 / 用时）不得因 isDebate 整段隐藏。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys } from "@/lib/queryKeys";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  isDebate,
  projectExecution,
  useExecutionStore,
} from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { StatusStrip } from "../StatusStrip";

const MID = "msg-debate-done-strip";

const debatePlan: ExecutionPlan = {
  id: "exec-debate-done",
  planType: "multi_agent",
  taskSummary: "该不该上微服务",
  agents: [
    { id: "a-pro", role: "正方" },
    { id: "a-con", role: "反方" },
  ],
  runs: [
    {
      id: "r-pro",
      agentId: "a-pro",
      task: "支持",
      dependsOn: [],
      stance: "pro",
      group: "g",
    },
    {
      id: "r-con",
      agentId: "a-con",
      task: "反对",
      dependsOn: [],
      stance: "con",
      group: "g",
    },
  ],
};

const doneFrames: RunFrame[] = [
  {
    t: 1,
    kind: "run_started",
    runId: "r-pro",
    agentId: "a-pro",
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  },
  {
    t: 2,
    kind: "run_completed",
    runId: "r-pro",
    agentId: "a-pro",
    outputSummary: "正方陈词",
    durationMs: 1_200,
    cost: {
      input: 0,
      cached: 0,
      output: 0,
      total: 50_000_000,
      currency: "USD",
      pricing_source: "curated",
    },
  },
  {
    t: 3,
    kind: "run_started",
    runId: "r-con",
    agentId: "a-con",
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  },
  {
    t: 4,
    kind: "run_completed",
    runId: "r-con",
    agentId: "a-con",
    outputSummary: "反方陈词",
    durationMs: 1_100,
    cost: {
      input: 0,
      cached: 0,
      output: 0,
      total: 40_000_000,
      currency: "USD",
      pricing_source: "curated",
    },
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

describe("StatusStrip · 辩论完成态 meta", () => {
  it("isDebate 完成态仍展示子任务进度（不含 Agent 数）", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    const exec = projectExecution(debatePlan, doneFrames, "completed");
    expect(isDebate(exec)).toBe(true);

    renderStrip(exec);

    expect(screen.queryByText(/个 Agent/)).toBeNull();
    expect(screen.getByText(/2\/2/)).toBeTruthy();
    expect(screen.queryByText(/\$/)).toBeNull();
    expect(screen.queryByText(/¥/)).toBeNull();
    expect(screen.queryByText(/已花/)).toBeNull();
    expect(screen.queryByText(/未计价/)).toBeNull();
  });
});
