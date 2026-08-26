// @vitest-environment jsdom
/**
 * User-stop paints「已停止」from the arbitrator (`showStripStopped`), not from
 * `execution.status==="cancelled"`. Rate-limit / partial / empty interrupt on a
 * cancelled status must not take the stopped face. Empty interrupt (`send_next`)
 * is idle chrome — no spinner「进行中」, no 已停止 / 失败. Stop is not an error.
 * 硬停改动入口不在状态条（右坞「改动」tab / 画布详情段）。
 * 整轮 Stop 在输入框，不在状态条。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { LLM_RATE_LIMIT_MESSAGE, LLM_RATE_LIMIT_WHY } from "@/lib/errors";
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
import { afterEach, describe, expect, it, vi } from "vitest";
import { StatusStrip } from "../StatusStrip";

const MID = "msg-stopped-strip";

type AssistantFace = {
  finishReason?: string | null;
  error?: { code: string; message: string } | null;
  isStreaming?: boolean;
};

let assistantFace: AssistantFace = { finishReason: "cancelled" };
let turnPhase: "idle" | "stopping" | "streaming" = "idle";

vi.mock("@/stores/conversation", async () => {
  const actual = await vi.importActual<typeof import("@/stores/conversation")>(
    "@/stores/conversation",
  );
  return {
    ...actual,
    useActiveGenerating: () => false,
    useActiveTurnPhase: () => turnPhase,
    useConversationStore: (
      sel: (s: {
        currentConversationId: string;
        stopGeneration: () => void;
        byId: Record<
          string,
          {
            messages: Array<{
              id: string;
              role: "assistant";
              content: string;
              createdAt: string;
              executionId: string;
              isStreaming: boolean;
              finishReason?: string | null;
              error?: { code: string; message: string } | null;
            }>;
          }
        >;
      }) => unknown,
    ) =>
      sel({
        currentConversationId: "conv-1",
        stopGeneration: () => {},
        byId: {
          "conv-1": {
            messages: [
              {
                id: MID,
                role: "assistant",
                content: "",
                createdAt: "",
                executionId: "exec-stopped",
                isStreaming: Boolean(assistantFace.isStreaming),
                finishReason: assistantFace.finishReason,
                error: assistantFace.error,
              },
            ],
          },
        },
      }),
    getActiveRuntime: () => ({ messages: [] }),
  };
});

vi.mock("@/services/turns", () => ({
  lastUserMessageId: () => null,
  runRegenerate: vi.fn(),
}));

const plan: ExecutionPlan = {
  id: "exec-stopped",
  planType: "multi_agent",
  taskSummary: "并行调研",
  agents: [{ id: "w1", role: "研究员" }],
  runs: [{ id: "r1", agentId: "w1", task: "调研", dependsOn: [] }],
};

const frames: RunFrame[] = [
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
    outputSummary: "完成调研",
    durationMs: 1000,
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
  assistantFace = { finishReason: "cancelled" };
  turnPhase = "idle";
  cleanup();
  useExecutionStore.setState({ byId: {} });
});

describe("StatusStrip · user stop cancelled", () => {
  it("user-stop face → 已停止, no 重试 / 继续 / spinner / stop / 排查包", () => {
    const exec = projectExecution(plan, frames, "cancelled");
    expect(exec.status).toBe("cancelled");

    const { container } = renderStrip(exec);

    expect(screen.getByText("已停止")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重试失败项" })).toBeNull();
    expect(screen.queryByRole("button", { name: "继续" })).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(screen.queryByLabelText("停止整轮")).toBeNull();
    expect(screen.queryByText(/改动 \d+ 个文件/)).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
  });

  it("cancelled status + 限流脸 → 失败条, 不画已停止", () => {
    assistantFace = {
      finishReason: "cancelled",
      error: { code: "LLM_RATE_LIMIT", message: LLM_RATE_LIMIT_MESSAGE },
    };
    const exec = projectExecution(plan, frames, "cancelled");
    renderStrip(exec);

    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.getByTestId("status-strip-failed")).toBeTruthy();
    expect(screen.getByText(LLM_RATE_LIMIT_MESSAGE)).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
  });

  it("cancelled status + productLanded 限流 → 部分完成, 不画已停止", () => {
    assistantFace = {
      finishReason: "cancelled",
      error: { code: "LLM_RATE_LIMIT", message: LLM_RATE_LIMIT_MESSAGE },
    };
    const landed: RunFrame[] = [
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
        kind: "run_failed",
        runId: "r1",
        agentId: "w1",
        error: LLM_RATE_LIMIT_MESSAGE,
        errorCode: "LLM_RATE_LIMIT",
        retryable: true,
        productLanded: true,
      },
    ];
    const exec = projectExecution(plan, landed, "cancelled");
    renderStrip(exec);

    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
    expect(screen.getByTestId("status-strip-partial")).toBeTruthy();
    expect(screen.getByText("部分完成")).toBeTruthy();
    expect(screen.queryByText(LLM_RATE_LIMIT_WHY)).toBeNull();
    expect(screen.queryByText(LLM_RATE_LIMIT_MESSAGE)).toBeNull();
    expect(screen.queryByTestId("status-strip-partial-reason")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("partial + 限流：条上不回潮交付 summary", () => {
    assistantFace = {
      finishReason: "cancelled",
      error: { code: "LLM_RATE_LIMIT", message: LLM_RATE_LIMIT_MESSAGE },
    };
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setDeliveryStatus(
      {
        execution_id: plan.id,
        state: "partial",
        summary: "未能交付：1 项未完成",
        delivered_files: [],
        gaps: [],
        actions: [],
      },
      MID,
    );
    const landed: RunFrame[] = [
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
        kind: "run_failed",
        runId: "r1",
        agentId: "w1",
        error: LLM_RATE_LIMIT_MESSAGE,
        errorCode: "LLM_RATE_LIMIT",
        retryable: true,
        productLanded: true,
      },
    ];
    const exec = projectExecution(plan, landed, "cancelled");
    renderStrip(exec);

    expect(screen.getByTestId("status-strip-partial")).toBeTruthy();
    expect(screen.getByText("部分完成")).toBeTruthy();
    expect(screen.queryByText("未能交付：1 项未完成")).toBeNull();
    expect(screen.queryByText(LLM_RATE_LIMIT_WHY)).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("generic partial：条上也不回潮交付 summary", () => {
    assistantFace = { finishReason: "error", error: null };
    useExecutionStore.getState().startExecution(plan, MID);
    useExecutionStore.getState().setDeliveryStatus(
      {
        execution_id: plan.id,
        state: "partial",
        summary: "未能交付：1 项未完成",
        delivered_files: [],
        gaps: [],
        actions: [],
      },
      MID,
    );
    const landed: RunFrame[] = [
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
        kind: "run_failed",
        runId: "r1",
        agentId: "w1",
        error: "模型调用失败",
        productLanded: true,
      },
    ];
    const exec = projectExecution(plan, landed, "failed");
    renderStrip(exec);

    expect(screen.getByTestId("status-strip-partial")).toBeTruthy();
    expect(screen.getByText("部分完成")).toBeTruthy();
    expect(screen.queryByText("未能交付：1 项未完成")).toBeNull();
  });

  it("empty interrupt → 不画已停止 / 失败条 / 进行中（判决在输入框）", () => {
    assistantFace = { finishReason: "interrupted", error: null };
    // Captain sink makes canPaintTeamCompleted false on cancelled — the old
    // fallback was RunningStrip (Loader2「进行中」). send_next must stay idle.
    const withCaptain: ExecutionPlan = {
      ...plan,
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
    const captainFrames: RunFrame[] = [
      {
        t: 0,
        kind: "run_started",
        runId: "captain",
        agentId: "ceo",
        parentRunId: null,
        runKind: "captain",
        continuesRunId: null,
      },
      ...frames,
    ];
    const exec = projectExecution(withCaptain, captainFrames, "cancelled");
    const { container } = renderStrip(exec);

    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(screen.queryByLabelText("进行中")).toBeNull();
  });

  it("stopping：可见停止中（图仍 running）", () => {
    turnPhase = "stopping";
    assistantFace = { isStreaming: true };
    const startedOnly: RunFrame[] = [
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
    const exec = projectExecution(plan, startedOnly, "running");
    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-stopping")).toBeTruthy();
    expect(screen.getByText("停止中")).toBeTruthy();
    expect(screen.queryByText("已停止")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });

  it("工人全终态 + 图 cancelled、气泡尚无 finishReason → 已停止（不等 message_end）", () => {
    turnPhase = "stopping";
    assistantFace = { isStreaming: true };
    const exec = projectExecution(plan, frames, "cancelled");
    const { container } = renderStrip(exec);

    expect(screen.getByText("已停止")).toBeTruthy();
    expect(screen.queryByText("停止中")).toBeNull();
    expect(screen.queryByTestId("status-strip-stopping")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
    expect(screen.queryByTestId("status-strip-partial")).toBeNull();
  });
});
