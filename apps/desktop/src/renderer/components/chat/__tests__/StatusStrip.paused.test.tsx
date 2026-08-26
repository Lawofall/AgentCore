// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys } from "@/lib/queryKeys";
import { useConversationStore } from "@/stores/conversation";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  projectExecution,
  useExecutionStore,
} from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StatusStrip } from "../StatusStrip";

const MID = "msg-paused-strip";

const plan: ExecutionPlan = {
  id: "exec-paused",
  planType: "multi_agent",
  taskSummary: "并行调研",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "w2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r2", agentId: "w2", task: "撰写", dependsOn: ["r1"] },
  ],
};

const waveDoneFrames: RunFrame[] = [
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
    kind: "run_completed",
    runId: "r1",
    agentId: "w1",
    outputSummary: "调研完成",
    durationMs: 100,
  },
];

const firstBatchStillRunning: RunFrame[] = [
  {
    t: 1,
    kind: "run_started",
    runId: "r1",
    agentId: "w1",
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
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
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("StatusStrip · paused", () => {
  it("plan_review 挂起（无 unsettled）显示静态暂停条，保留 M/N，不转圈、无解说句", () => {
    const exec = projectExecution(plan, waveDoneFrames, "paused");
    expect(exec.progress).toEqual({ completed: 1, total: 2 });

    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-paused")).toBeTruthy();
    expect(screen.getByLabelText("已暂停")).toBeTruthy();
    expect(screen.queryByText(/等待你确认/)).toBeNull();
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });

  it("team_preview 开工挂起（paused + 全 pending）同样静态暂停条，不转圈", () => {
    const exec = projectExecution(plan, [], "paused");
    expect(exec.runs.every((r) => r.status === "pending")).toBe(true);

    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-paused")).toBeTruthy();
    expect(screen.getByLabelText("已暂停")).toBeTruthy();
    expect(screen.queryByText(/等待你确认/)).toBeNull();
    expect(screen.getByText("0/2")).toBeTruthy();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });

  it("captain 已开、工人仍 pending：静态暂停条，不冒「新批次待确认」", () => {
    const withCaptain: ExecutionPlan = {
      ...plan,
      id: "exec-paused-captain",
      agents: [{ id: "ceo", role: "CEO" }, ...plan.agents],
      runs: [
        {
          id: "captain",
          agentId: "ceo",
          task: "",
          dependsOn: [],
          kind: "captain",
        },
        ...plan.runs,
      ],
    };
    const exec = projectExecution(
      withCaptain,
      [
        {
          t: 1,
          kind: "run_started",
          runId: "captain",
          agentId: "ceo",
          parentRunId: null,
          runKind: "captain",
          continuesRunId: null,
        },
      ],
      "paused",
    );
    expect(exec.runs.find((r) => r.id === "captain")?.status).toBe("running");

    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-paused")).toBeTruthy();
    expect(screen.queryByTestId("status-strip-pending-batch")).toBeNull();
    expect(screen.queryByTestId("status-strip-pending-batch-badge")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });

  it("增量开工卡：paused + 第一批仍 running → 运行条 +「新批次待确认」徽标", () => {
    const exec = projectExecution(plan, firstBatchStillRunning, "paused");
    expect(exec.runs.some((r) => r.status === "running")).toBe(true);

    const { container } = renderStrip(exec);

    expect(screen.queryByTestId("status-strip-paused")).toBeNull();
    expect(screen.getByTestId("status-strip-pending-batch")).toBeTruthy();
    expect(
      screen.getByTestId("status-strip-pending-batch-badge").textContent,
    ).toBe("新批次待确认");
    expect(screen.queryByText("并行调研")).toBeNull();
    expect(screen.getByText("0/2")).toBeTruthy();
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });

  it("闸卡暂停不点亮「继续」（outcome 未 attested）", () => {
    const exec = projectExecution(plan, waveDoneFrames, "paused");
    renderStrip(exec);
    expect(screen.getByTestId("status-strip-paused")).toBeTruthy();
    expect(screen.queryByTestId("paused-continue-action")).toBeNull();
    expect(screen.queryByText("继续")).toBeNull();
  });

  it("attested paused：已暂停 + 继续，不画失败条", () => {
    useConversationStore.getState().switchConversation("conv-paused-continue");
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setAttestedOutcome("paused", MID);
    const exec = projectExecution(plan, waveDoneFrames, "failed");
    renderStrip(exec);
    expect(screen.getByTestId("status-strip-paused")).toBeTruthy();
    expect(screen.getByTestId("paused-continue-action")).toBeTruthy();
    expect(screen.getByText("已暂停")).toBeTruthy();
    expect(screen.getByText("继续")).toBeTruthy();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
    expect(screen.queryByText("失败")).toBeNull();
  });

  it("running 仍走转圈分支（回归）", () => {
    const exec = projectExecution(plan, waveDoneFrames.slice(0, 1), "running");
    const { container } = renderStrip(exec);

    expect(screen.queryByTestId("status-strip-paused")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });
});
