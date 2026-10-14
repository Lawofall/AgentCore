// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  useExecutionStore,
} from "@/stores/execution";
import { useRunStopPendingStore } from "@/stores/runStopPending";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

  it("node face names the segment and omits a running status word", () => {
    renderNodeFace();
    expect(screen.getByText("研究员")).toBeTruthy();
    expect(screen.queryByText(/^执行中/)).toBeNull();
    expect(screen.queryByText("停止请求中…")).toBeNull();
  });

  it("node status line does not show stop-pending copy for settled workers", () => {
    useRunStopPendingStore.getState().markPending("exec-stop", "r1");
    renderNodeFace(nodeData({ status: "completed", isAnimating: false }));
    expect(screen.queryByText("停止请求中…")).toBeNull();
    expect(screen.queryByText(/^已完成/)).toBeNull();
    expect(screen.getByText("研究员")).toBeTruthy();
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
});
