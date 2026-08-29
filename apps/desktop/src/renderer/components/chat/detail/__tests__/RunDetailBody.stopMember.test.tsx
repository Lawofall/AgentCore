// @vitest-environment jsdom
/**
 * RunDetailBody 按人干预入口：只在 `isLiveRunStatus`（running / pending）时出现；
 * 终局整条不渲染、不写灰字原因、不留空 wrapper。点击走 requestRunStop；请求中禁用。
 *
 * 两条诚实边界一并钉在这里：受理与否由服务端回答（引擎够不着时不留「停止请求中…」），
 * 以及 captain 那一路不出按人干预（主管就是这条对话本身，「只停这位队员」对它无意义）。
 */
import { RunDetailBody } from "@/components/chat/detail/RunDetailBody";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgentState, Execution, RunNode } from "@/stores/execution";
import { useRunStopPendingStore } from "@/stores/runStopPending";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const submitRunStop = vi.fn();

const convPhase = vi.hoisted(() => ({
  turnPhase: "streaming" as string,
}));

vi.mock("@/services/runStop", () => ({
  submitRunStop: (...args: unknown[]) => submitRunStop(...args),
}));

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useMessageExecution: () => mockExecution,
  };
});

vi.mock("@/stores/conversation", () => {
  const useConversationStore = (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      currentConversationId: "c1",
      messages: [{ id: "m1", isStreaming: true, collab: null, traceId: null }],
      stopGeneration: vi.fn(),
    });
  (useConversationStore as unknown as { getState: () => object }).getState =
    () => ({ currentConversationId: "c1", byId: {} });
  return {
    useConversationStore,
    activeRuntime: (s: { messages: unknown[] }) => s,
    runtimeOf: () => ({ toolStartedMs: {}, turnPhase: convPhase.turnPhase }),
  };
});

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ showRunDetail: vi.fn() }),
}));

vi.mock("@/stores/ui", () => ({
  useUIStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ diagnosticMode: false }),
  turnDetailPath: () => "/t",
}));

vi.mock("@/hooks/useTurnAudit", () => ({
  useTurnAudit: () => ({ data: null }),
}));

vi.mock("@/hooks/useRunLlmWindow", () => ({
  useRunLlmWindow: () => ({ data: null, loading: false, error: null }),
}));

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

/** 引擎受理了这次停止（服务端回执的正常形）。 */
const ACCEPTED = {
  queued: 1,
  accepted: true,
  reason: "queued",
  detail: "已交给引擎：正在停这位队员。",
};

afterEach(cleanup);

const baseAgent: AgentState = {
  id: "w1",
  role: "调研员",
  thinking: false,
  status: "idle",
  currentRunId: null,
  outputChunks: [],
  reasoningChunks: [],
  toolCalls: [],
  toolProgress: null,
  toolExecutionLive: null,
};

const baseRun: RunNode = {
  id: "r1",
  agentId: "w1",
  task: "调研竞品",
  status: "pending",
  dependsOn: [],
  outputSummary: null,
  outputFiles: [],
  debrief: null,
  durationMs: null,
  startedAt: null,
  error: null,
  parentRunId: null,
  kind: "agent",
  role: "member",
  model: null,
  usage: null,
  cost: null,
  stance: null,
  group: null,
  round: 0,
  sideKey: null,
  continuesRunId: null,
  continuationIndex: 0,
  revised: null,
  replacesRunId: null,
  checkpoint: null,
  receivedContext: [],
  escalations: [],
  process: [],
};

let mockExecution: Execution = {
  id: "exec1",
  planType: "multi_agent",
  taskSummary: "调研竞品",
  status: "running",
  agents: [baseAgent],
  runs: [baseRun],
  acts: [
    {
      actId: "act-1",
      kind: "multi_agent",
      title: null,
      anchorRunId: null,
      authorizedBy: null,
    },
  ],
  progress: { completed: 0, total: 1 },
  batches: [],
  debate: null,
  debateRounds: [],
  crossExamEnabled: false,
  debateOpening: null,
  debatePretrial: null,
};

function seed(opts: {
  agentStatus: AgentState["status"];
  runStatus: RunNode["status"];
  runKind?: RunNode["kind"];
  replacesRunId?: string | null;
}) {
  mockExecution = {
    ...mockExecution,
    agents: [
      {
        ...baseAgent,
        status: opts.agentStatus,
        currentRunId: opts.runStatus === "running" ? "r1" : null,
      },
    ],
    runs: [
      {
        ...baseRun,
        status: opts.runStatus,
        kind: opts.runKind ?? "agent",
        replacesRunId: opts.replacesRunId ?? null,
      },
    ],
  };
}

function wrap(ui: ReactElement) {
  return render(
    <TooltipProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </TooltipProvider>,
  );
}

describe("RunDetailBody member stop", () => {
  beforeEach(() => {
    convPhase.turnPhase = "streaming";
    submitRunStop.mockReset();
    submitRunStop.mockResolvedValue(ACCEPTED);
    useRunStopPendingStore.getState().reset();
  });

  it("shows stop for pending (queued) without live output banner", () => {
    seed({ agentStatus: "idle", runStatus: "pending" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    expect(screen.queryByText(/正在实时输出/)).toBeNull();
    expect(screen.queryByRole("button", { name: "停止整轮" })).toBeNull();
  });

  it("shows per-member stop and redirect while working, without 停止整轮", () => {
    seed({ agentStatus: "working", runStatus: "running" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    expect(screen.queryByText(/正在实时输出/)).toBeNull();
    expect(screen.queryByText("接手")).toBeNull();
    expect(screen.queryByRole("button", { name: /记下改法/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "停止整轮" })).toBeNull();
  });

  it("shows 接手 chip when this run replaces another, without live-output copy", () => {
    seed({
      agentStatus: "working",
      runStatus: "running",
      replacesRunId: "r0",
    });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    const chip = screen.getByText("接手");
    expect(chip.getAttribute("title")).toMatch(/同角色新人/);
    expect(
      screen.queryByText(/接手重写|正在实时输出|同一人接续|辩论主持/),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
  });

  it("keeps 接手 chip after the replacement run finishes", () => {
    seed({
      agentStatus: "completed",
      runStatus: "completed",
      replacesRunId: "r0",
    });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    expect(screen.getByText("接手")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /停止这位队员/ })).toBeNull();
  });

  it.each(["completed", "failed", "cancelled", "skipped"] as const)(
    "does not render per-member intervene once %s (no gray reason, no empty wrapper)",
    (runStatus) => {
      seed({
        agentStatus: runStatus === "completed" ? "completed" : "idle",
        runStatus,
      });
      wrap(<RunDetailBody messageId="m1" runId="r1" />);

      expect(screen.queryByRole("button", { name: /停止这位队员/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /立即改此人/ })).toBeNull();
      expect(
        screen.queryByText(/已经跑完|已经结束|已经停下|没有执行|还没开工/),
      ).toBeNull();
      expect(submitRunStop).not.toHaveBeenCalled();
    },
  );

  it("explains that a queued member has no in-flight work to redirect", () => {
    seed({ agentStatus: "idle", runStatus: "pending" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    // 排队中可停（可用），但没有在跑的工作可改（不可用 + 原因）。
    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    const redirect = screen.getByRole("button", { name: /^立即改此人（/ });
    expect(redirect.getAttribute("aria-disabled")).toBe("true");
    expect(redirect.getAttribute("title")).toMatch(/还没开工/);
  });

  it("click requests node-scoped stop and disables while pending", async () => {
    seed({ agentStatus: "working", runStatus: "running" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    const btn = screen.getByRole("button", { name: "停止这位队员" });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(submitRunStop).toHaveBeenCalledWith("c1", {
        executionId: "exec1",
        runId: "r1",
      });
    });
    const busy = screen.getByRole("button", { name: "停止请求中…" });
    expect(busy).toBeTruthy();
    expect((busy as HTMLButtonElement).disabled).toBe(true);
  });

  // 服务端说够不着（驱动已退出 / run 不在当前计划里）时**什么都没入队**。留一个
  // 「停止请求中…」在屏上就是替引擎撒谎——用户会一直等一个永远不来的确认。
  it("keeps no in-flight state when the engine says it cannot reach the run", async () => {
    submitRunStop.mockResolvedValue({
      queued: 0,
      accepted: false,
      reason: "no_live_drive",
      detail: "这批工作已经不在引擎手里了，没有能停的在跑队员。",
    });
    seed({ agentStatus: "working", runStatus: "running" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    fireEvent.click(screen.getByRole("button", { name: "停止这位队员" }));

    await waitFor(() => {
      expect(submitRunStop).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "停止请求中…" })).toBeNull();
    });
    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    expect(useRunStopPendingStore.getState().isRunCovered("exec1", "r1")).toBe(
      false,
    );
  });

  // captain 不是被派出去的队员——引擎的计划里没有它，「只停这位队员」必然落空。
  // 整轮停只挂在 captain 详情（队员栏不夹），与输入框「停止生成」同一条 stopGeneration。
  it("hides per-member intervene on the captain run, keeps 停止整轮", () => {
    seed({ agentStatus: "working", runStatus: "running", runKind: "captain" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    expect(screen.queryByRole("button", { name: /停止这位队员/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /立即改此人/ })).toBeNull();
    expect(screen.getByRole("button", { name: "停止整轮" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /记下改法/ })).toBeNull();
  });

  it("hides 停止这位队员 while the whole turn is stopping", () => {
    convPhase.turnPhase = "stopping";
    seed({ agentStatus: "working", runStatus: "running" });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    expect(screen.queryByRole("button", { name: /停止这位队员/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /停止请求中/ })).toBeNull();
  });

  it("does not list DAG neighbors — topology stays on the collab graph", () => {
    seed({ agentStatus: "idle", runStatus: "completed" });
    mockExecution = {
      ...mockExecution,
      agents: [
        { ...baseAgent, id: "ceo", role: "CEO", currentRunId: null },
        { ...baseAgent, id: "w1", role: "调研员", currentRunId: null },
        { ...baseAgent, id: "w2", role: "方案总纲整合", currentRunId: null },
      ],
      runs: [
        {
          ...baseRun,
          id: "ceo",
          agentId: "ceo",
          kind: "captain",
          role: "ceo",
          task: "统筹",
          status: "completed",
          parentRunId: null,
          dependsOn: [],
        },
        {
          ...baseRun,
          id: "r0",
          agentId: "w2",
          task: "方案总纲整合",
          status: "completed",
          parentRunId: "ceo",
          dependsOn: [],
        },
        {
          ...baseRun,
          id: "r1",
          agentId: "w1",
          task: "调研竞品",
          status: "completed",
          parentRunId: "ceo",
          dependsOn: ["r0"],
        },
        {
          ...baseRun,
          id: "r2",
          agentId: "w2",
          task: "后续整合",
          status: "completed",
          parentRunId: "r1",
          dependsOn: ["r1"],
        },
      ],
    };
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    expect(screen.queryByText("关系")).toBeNull();
    expect(screen.queryByText("依赖")).toBeNull();
    expect(screen.queryByText("后续")).toBeNull();
    expect(screen.queryByText("上级")).toBeNull();
    expect(screen.queryByText(/子任务/)).toBeNull();
    expect(screen.queryByText("数据从哪来")).toBeNull();
  });
});
