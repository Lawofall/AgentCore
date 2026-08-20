// @vitest-environment jsdom
/**
 * 运行态状态条「用时」走墙钟自增（ToolLine useRunningElapsed 同形），
 * 不读 elapsedMs(frames) 跨度——长工具无新帧时跨度会冻住。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys } from "@/lib/queryKeys";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  elapsedMs,
  projectExecution,
  useExecutionStore,
} from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StatusStrip } from "../StatusStrip";

const MID = "msg-running-elapsed";

const plan: ExecutionPlan = {
  id: "exec-running-elapsed",
  planType: "multi_agent",
  taskSummary: "调研",
  agents: [
    { id: "a1", role: "调研员" },
    { id: "a2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "a1", task: "调研竞品", dependsOn: [] },
    { id: "r2", agentId: "a2", task: "写建议", dependsOn: ["r1"] },
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
            execution={projectExecution(plan, frames, "running")}
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
  vi.useRealTimers();
});
beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("StatusStrip · 运行态墙钟用时", () => {
  it("长工具无新帧时显示墙钟用时，不显示冻结跨度", () => {
    vi.useFakeTimers();
    const now = 1_700_000_040_000;
    vi.setSystemTime(now);
    const frames: RunFrame[] = [
      started("r1", "a1", now - 40_000),
      started("r2", "a2", now - 38_000),
    ];
    // 帧流跨度只有 2s；墙钟已经过了 40s。
    expect(elapsedMs(frames)).toBe(2_000);
    renderStrip(frames);
    expect(screen.getByText("0/2 · 用时 40s")).toBeTruthy();
    expect(screen.queryByText(/用时 2s/)).toBeNull();
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByText("0/2 · 用时 41s")).toBeTruthy();
  });

  it("每秒自增，折叠重挂不归零", () => {
    vi.useFakeTimers();
    const now = 1_700_000_005_000;
    vi.setSystemTime(now);
    const frames: RunFrame[] = [started("r1", "a1", now - 5_000)];
    const { unmount } = renderStrip(frames);
    expect(screen.getByText("0/2 · 用时 5s")).toBeTruthy();
    unmount();
    vi.advanceTimersByTime(3_000);
    renderStrip(frames);
    expect(screen.getByText("0/2 · 用时 8s")).toBeTruthy();
  });

  it("没有帧则不显示用时", () => {
    renderStrip([]);
    expect(screen.getByText("0/2")).toBeTruthy();
    expect(screen.queryByText(/用时/)).toBeNull();
  });
});
