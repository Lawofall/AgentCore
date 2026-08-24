// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  useExecutionStore,
} from "@/stores/execution";
import { useRunStopPendingStore } from "@/stores/runStopPending";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GraphTeamStopControl } from "../GraphTeamStopControl";
import { AgentNodeCardFace } from "../agentNode/AgentNodeFace";
import { buildAgentNodePresentation } from "../agentNode/presentation";
import type { AgentNodeData } from "../agentNode/shared";

const MID = "msg-run-stop";
const CID = "conv-run-stop";

const convPhase = vi.hoisted(() => ({
  turnPhase: "streaming" as string,
}));

const submitRunStop = vi.fn();

vi.mock("@/services/runStop", () => ({
  submitRunStop: (...args: unknown[]) => submitRunStop(...args),
}));

vi.mock("@/stores/conversation", () => {
  const useConversationStore = (
    sel: (s: { currentConversationId: string }) => unknown,
  ) => sel({ currentConversationId: CID });
  (useConversationStore as unknown as { getState: () => object }).getState =
    () => ({ currentConversationId: CID, byId: {} });
  return {
    useConversationStore,
    useActiveTurnPhase: () => convPhase.turnPhase,
    runtimeOf: () => ({ turnPhase: convPhase.turnPhase }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

/** 引擎受理了这次停止（服务端回执的正常形）。 */
const ACCEPTED = { queued: 1, accepted: true, reason: "queued", detail: "" };

const plan: ExecutionPlan = {
  id: "exec-stop",
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
];

function seedRunningExecution() {
  useExecutionStore.setState({ byId: {} });
  useExecutionStore.getState().startExecution(plan, MID);
  for (const f of runningFrames) {
    useExecutionStore.getState().recordFrame(f, MID);
  }
}

function nodeData(overrides: Partial<AgentNodeData> = {}): AgentNodeData {
  return {
    agentId: "w1",
    role: "研究员",
    runId: "r1",
    status: "running",
    isAnimating: true,
    task: "调研",
    outputPreview: "",
    tokenCount: 0,
    toolCount: 0,
    focused: false,
    onActivate: vi.fn(),
    ...overrides,
  };
}

function wrap(ui: ReactElement) {
  return render(
    <TooltipProvider>
      <ExecutionScopeContext.Provider value={MID}>
        {ui}
      </ExecutionScopeContext.Provider>
    </TooltipProvider>,
  );
}

function renderNodeFace(d: AgentNodeData = nodeData()) {
  const p = buildAgentNodePresentation(d);
  wrap(
    <AgentNodeCardFace
      d={d}
      p={p}
      flashColor="var(--success)"
      flashing={false}
    />,
  );
  return p;
}

describe("graph run-stop entries", () => {
  beforeEach(() => {
    convPhase.turnPhase = "streaming";
    submitRunStop.mockReset();
    submitRunStop.mockResolvedValue(ACCEPTED);
    useRunStopPendingStore.getState().reset();
    seedRunningExecution();
  });

  afterEach(() => {
    cleanup();
  });

  it("node status line shows 停止请求中… when run is stop-covered, without pretending cancelled", () => {
    useRunStopPendingStore.getState().markPending("exec-stop", "r1");
    renderNodeFace();

    expect(screen.getByText("停止请求中…")).toBeTruthy();
    expect(screen.queryByText(/^执行中/)).toBeNull();
    expect(screen.queryByRole("button", { name: "停止这个" })).toBeNull();
    expect(screen.queryByRole("button", { name: "改方向" })).toBeNull();
    // Status must stay running — optimistic UI does not pretend cancelled.
    expect(useExecutionStore.getState().byId[MID]?.status).toBe("running");
  });

  it("node status line keeps running copy when not stop-covered", () => {
    renderNodeFace();
    expect(screen.getByText(/^执行中/)).toBeTruthy();
    expect(screen.queryByText("停止请求中…")).toBeNull();
  });

  it("node status line shows 停止中… while the whole turn is stopping", () => {
    convPhase.turnPhase = "stopping";
    renderNodeFace();
    expect(screen.getByText("停止中…")).toBeTruthy();
    expect(screen.queryByText(/^执行中/)).toBeNull();
  });

  it("node status line does not show stop-pending copy for settled workers", () => {
    useRunStopPendingStore.getState().markPending("exec-stop", "r1");
    renderNodeFace(nodeData({ status: "completed", isAnimating: false }));
    expect(screen.queryByText("停止请求中…")).toBeNull();
    expect(screen.getByText(/^已完成/)).toBeTruthy();
  });

  // 卡面是纯展示：按人干预只在右坞 run 详情，图节点不挂改方向 / 停止。
  it("node face has no stop/redirect action buttons", () => {
    renderNodeFace();
    expect(screen.queryByRole("button", { name: "停止这个" })).toBeNull();
    expect(screen.queryByRole("button", { name: "改方向" })).toBeNull();
    expect(screen.queryByRole("button", { name: "停止请求中…" })).toBeNull();
  });

  it("team-wide pending covers node status line the same way", () => {
    useRunStopPendingStore.getState().markPending("exec-stop", null);
    renderNodeFace();
    expect(screen.getByText("停止请求中…")).toBeTruthy();
    expect(screen.queryByText(/^执行中/)).toBeNull();
  });

  it("team entry calls submitRunStop without run scope and keeps honest pending", async () => {
    wrap(<GraphTeamStopControl />);

    const btn = screen.getByRole("button", { name: "停止任务" });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(submitRunStop).toHaveBeenCalledWith(CID, {
        executionId: "exec-stop",
        runId: null,
      });
    });
    expect(screen.getByRole("button", { name: "停止请求中…" })).toBeTruthy();
    expect(useExecutionStore.getState().byId[MID]?.status).toBe("running");
  });

  // 整队停止同样按服务端回答：引擎手里已经没有这批工作时，不留「停止请求中…」。
  it("team entry clears pending when the engine has no live drive", async () => {
    submitRunStop.mockResolvedValue({
      queued: 0,
      accepted: false,
      reason: "no_live_drive",
      detail: "这批工作已经不在引擎手里了，没有能停的在跑队员。",
    });
    wrap(<GraphTeamStopControl />);

    fireEvent.click(screen.getByRole("button", { name: "停止任务" }));

    await waitFor(() => {
      expect(submitRunStop).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "停止请求中…" })).toBeNull();
    });
    expect(useRunStopPendingStore.getState().isPending("exec-stop", null)).toBe(
      false,
    );
  });

  it("team entry hides when no active workers remain", () => {
    useExecutionStore.setState({ byId: {} });
    const single: ExecutionPlan = {
      id: "exec-done",
      planType: "multi_agent",
      taskSummary: "单人",
      agents: [{ id: "w1", role: "研究员" }],
      runs: [{ id: "r1", agentId: "w1", task: "调研", dependsOn: [] }],
    };
    useExecutionStore.getState().startExecution(single, MID);
    useExecutionStore.getState().recordFrame(
      {
        t: 1,
        kind: "run_started",
        runId: "r1",
        agentId: "w1",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
      },
      MID,
    );
    useExecutionStore.getState().recordFrame(
      {
        t: 2,
        kind: "run_completed",
        runId: "r1",
        agentId: "w1",
        outputSummary: "done",
        durationMs: 10,
      },
      MID,
    );

    wrap(<GraphTeamStopControl />);
    expect(screen.queryByRole("button", { name: "停止任务" })).toBeNull();
  });

  it("hides the team entry while the whole turn is stopping", () => {
    convPhase.turnPhase = "stopping";
    wrap(<GraphTeamStopControl />);
    expect(screen.queryByRole("button", { name: "停止任务" })).toBeNull();
  });
});
