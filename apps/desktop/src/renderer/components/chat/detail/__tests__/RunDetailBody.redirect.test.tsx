// @vitest-environment jsdom
/**
 * 「改方向」成功提示必须诚实：后端 `drive_redirect` 收到请求即取消这名队员在飞的工作，
 * 并优先带现场热续跑（接不上才同角色换人重做）。旧文案说「调度器将在下一步接管（当前为
 * 排队阶段）」，等于告诉用户什么都还没发生——用户据此可能再点一次，而在飞工作其实已经
 * 被取消、正在重跑，要重新花时间和钱。
 */
import { RunDetailBody } from "@/components/chat/detail/RunDetailBody";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgentState, Execution, RunNode } from "@/stores/execution";
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

const submitRunRedirect = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastWarning = vi.fn();

/** 引擎受理了这次干预（服务端回执的正常形）。 */
const ACCEPTED = { queued: 1, accepted: true, reason: "queued", detail: "" };

vi.mock("@/services/runRedirect", () => ({
  submitRunRedirect: (...args: unknown[]) => submitRunRedirect(...args),
}));

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useMessageExecution: () => mockExecution,
  };
});

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      currentConversationId: "c1",
      messages: [{ id: "m1", isStreaming: true, collab: null, traceId: null }],
      stopGeneration: vi.fn(),
    }),
  activeRuntime: (s: { messages: unknown[] }) => s,
  runtimeOf: () => ({ toolStartedMs: {} }),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ showRunDetail: vi.fn() }),
}));

vi.mock("@/stores/ui", () => ({
  turnDetailPath: () => "/t",
}));

vi.mock("@/hooks/useTurnAudit", () => ({
  useTurnAudit: () => ({ data: null }),
}));

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

vi.mock("sonner", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
    warning: (...args: unknown[]) => toastWarning(...args),
  },
}));

afterEach(cleanup);

const workingAgent: AgentState = {
  id: "w1",
  role: "调研员",
  thinking: false,
  status: "working",
  currentRunId: "r1",
  outputChunks: ["初稿写到一半"],
  reasoningChunks: [],
  toolCalls: [],
  toolProgress: null,
  toolExecutionLive: null,
};

const runningRun: RunNode = {
  id: "r1",
  agentId: "w1",
  task: "调研竞品",
  status: "running",
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

const mockExecution: Execution = {
  id: "exec1",
  planType: "multi_agent",
  taskSummary: "调研竞品",
  status: "running",
  agents: [workingAgent],
  runs: [runningRun],
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

function wrap(ui: ReactElement) {
  return render(
    <TooltipProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </TooltipProvider>,
  );
}

/** Open the redirect composer, type a direction, submit. */
function submitDirection(text: string): void {
  fireEvent.click(screen.getByRole("button", { name: "立即改此人" }));
  fireEvent.change(screen.getByPlaceholderText("具体、可执行的修改方向…"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "提交改方向" }));
}

describe("RunDetailBody 改方向确认文案", () => {
  beforeEach(() => {
    submitRunRedirect.mockReset();
    submitRunRedirect.mockResolvedValue(ACCEPTED);
    toastSuccess.mockReset();
    toastError.mockReset();
    toastWarning.mockReset();
  });

  it("说清在飞工作已取消 + 正在重跑 + 要重新花钱，不谎报「还在排队」", async () => {
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    submitDirection("改成只看国内竞品");

    await waitFor(() => {
      expect(submitRunRedirect).toHaveBeenCalledWith("c1", {
        executionId: "exec1",
        runId: "r1",
        feedback: "改成只看国内竞品",
      });
    });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());

    const [title, opts] = toastSuccess.mock.calls[0] as [
      string,
      { description?: string },
    ];
    const copy = `${title}\n${opts?.description ?? ""}`;
    expect(copy).toContain("已取消");
    expect(copy).toContain("重跑");
    expect(copy).toMatch(/钱|计费|费用/);
    // 后端立刻取消 + 热续跑：不得再暗示「什么都没发生 / 还在排队」。
    expect(copy).not.toContain("排队");
    expect(copy).not.toContain("下一步接管");
  });

  it("引擎够不着这个 run：照回执原话说，不谎报「已改方向」", async () => {
    submitRunRedirect.mockResolvedValue({
      queued: 0,
      accepted: false,
      reason: "no_live_drive",
      detail: "这批工作的引擎已经退出，改不到了。",
    });
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    submitDirection("改成只看国内竞品");

    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalled();

    const [, opts] = toastWarning.mock.calls[0] as [
      string,
      { description?: string },
    ];
    expect(opts?.description).toBe("这批工作的引擎已经退出，改不到了。");
  });

  it("提交失败仍走错误提示，不留假成功", async () => {
    submitRunRedirect.mockRejectedValue(new Error("boom"));
    wrap(<RunDetailBody messageId="m1" runId="r1" />);

    submitDirection("改方向");

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
