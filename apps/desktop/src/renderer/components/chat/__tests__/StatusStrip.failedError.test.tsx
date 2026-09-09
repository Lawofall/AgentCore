// @vitest-environment jsdom
/**
 * FailureStrip is the same thin scoreboard as completed (失败 + n/m).
 * Task brief / curated failure sentence / engine jargon stay off the strip —
 * they live on the node face and dock. No expand/collapse residual.
 */
import { failureDetailSentence } from "@/components/graph/agentNode/shared";
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys } from "@/lib/queryKeys";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  type RunFrame,
  projectExecution,
} from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StatusStrip } from "../StatusStrip";

const MID = "msg-failed-error-strip";
const INTERRUPT_COPY = "模型响应中断，已保留已生成内容，可继续。";
const GATE_ERROR = "缺少必备章节：结论";

let sessionError: string | null = null;

vi.mock("@/stores/conversation", async () => {
  const actual = await vi.importActual<typeof import("@/stores/conversation")>(
    "@/stores/conversation",
  );
  return {
    ...actual,
    useActiveGenerating: () => false,
    useActiveTurnPhase: () => "idle",
    useActiveError: () => sessionError,
    useConversationStore: (
      sel: (s: {
        currentConversationId: string;
        stopGeneration: () => void;
      }) => unknown,
    ) =>
      sel({
        currentConversationId: "conv-1",
        stopGeneration: () => {},
      }),
    getActiveRuntime: () => ({ messages: [] }),
  };
});

vi.mock("@/services/turns", () => ({
  lastUserMessageId: () => null,
  runRegenerate: vi.fn(),
}));

const plan: ExecutionPlan = {
  id: "exec-failed-strip",
  planType: "multi_agent",
  taskSummary: "并行调研",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "ceo", role: "CEO 汇总" },
  ],
  runs: [
    { id: "r1", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r-ceo", agentId: "ceo", task: "汇总", dependsOn: ["r1"] },
  ],
};

/** execution=failed but no run_failed frame → no failedRun.error. */
const completedOnlyFrames: RunFrame[] = [
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

const failedWithErrorFrames: RunFrame[] = [
  {
    t: 1,
    kind: "run_started",
    runId: "r-ceo",
    agentId: "ceo",
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  },
  {
    t: 2,
    kind: "run_failed",
    runId: "r-ceo",
    agentId: "ceo",
    error: "工具超时：web_search",
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
  sessionError = null;
  cleanup();
});

describe("StatusStrip · FailureStrip scoreboard", () => {
  it("failed run → 失败 + n/m, not the task brief or curated sentence", () => {
    sessionError = INTERRUPT_COPY;
    const exec = projectExecution(plan, failedWithErrorFrames, "failed");
    const failed = exec.runs.find((r) => r.status === "failed");
    expect(failed?.error).toBe("工具超时：web_search");

    renderStrip(exec);

    expect(screen.getByTestId("status-strip-failed")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(
      screen.getByText(`${exec.progress.completed}/${exec.progress.total}`),
    ).toBeTruthy();
    expect(screen.queryByText("工具超时：web_search")).toBeNull();
    expect(screen.queryByText("CEO 汇总")).toBeNull();
    expect(screen.queryByText(failureDetailSentence(null, null))).toBeNull();
    expect(screen.queryByText(INTERRUPT_COPY)).toBeNull();
    expect(screen.queryByText("未获取到具体错误信息。")).toBeNull();
    expect(
      screen.queryByTestId("status-strip-failed-detail-toggle"),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("session interrupt copy and empty fallback stay off the strip", () => {
    sessionError = INTERRUPT_COPY;
    const exec = projectExecution(plan, completedOnlyFrames, "failed");
    expect(exec.status).toBe("failed");
    expect(exec.runs.find((r) => r.status === "failed")).toBeUndefined();

    renderStrip(exec);

    expect(screen.getByTestId("status-strip-failed")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.queryByText(INTERRUPT_COPY)).toBeNull();
    expect(screen.queryByText("未获取到具体错误信息。")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("contract gate error never reaches the strip as engine jargon", () => {
    const frames: RunFrame[] = [
      {
        t: 1,
        kind: "run_started",
        runId: "r-ceo",
        agentId: "ceo",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
      },
      {
        t: 2,
        kind: "run_failed",
        runId: "r-ceo",
        agentId: "ceo",
        error: GATE_ERROR,
        failureKind: "format",
      },
    ];
    const exec = projectExecution(plan, frames, "failed");
    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-failed")).toBeTruthy();
    expect(container.textContent).not.toContain("缺少必备章节");
    expect(container.textContent).not.toContain(".audit.json");
    expect(
      screen.queryByText(failureDetailSentence("format", null)),
    ).toBeNull();
  });

  it("files already saved before the failure paint 部分完成, not 失败", () => {
    const frames: RunFrame[] = [
      {
        t: 1,
        kind: "run_started",
        runId: "r-ceo",
        agentId: "ceo",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
      },
      {
        t: 2,
        kind: "run_failed",
        runId: "r-ceo",
        agentId: "ceo",
        error: "ConnectError: upstream 503",
        failureKind: "call",
        productLanded: true,
      },
    ];
    const exec = projectExecution(plan, frames, "failed");
    const { container } = renderStrip(exec);

    expect(container.textContent).not.toContain("ConnectError");
    expect(screen.getByTestId("status-strip-partial")).toBeTruthy();
    expect(screen.getByText("部分完成")).toBeTruthy();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("long failed task brief is not dumped onto the strip", () => {
    const longTask = `${"对范围【AgentCore AI 功能全链审计】做只读代码审计。".repeat(8)}报告写到 AgentCore/文档/reviews/code-audit-1-server_conversation.md`;
    const longPlan: ExecutionPlan = {
      ...plan,
      runs: [
        {
          id: "r-audit",
          agentId: "w1",
          task: longTask,
          dependsOn: [],
        },
      ],
      agents: [{ id: "w1", role: "代码审计员" }],
    };
    const frames: RunFrame[] = [
      {
        t: 1,
        kind: "run_started",
        runId: "r-audit",
        agentId: "w1",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
      },
      {
        t: 2,
        kind: "run_failed",
        runId: "r-audit",
        agentId: "w1",
        error: GATE_ERROR,
      },
    ];
    const exec = projectExecution(longPlan, frames, "failed");
    const { container } = renderStrip(exec);

    expect(screen.getByTestId("status-strip-failed")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(container.textContent).not.toContain("只读代码审计");
    expect(container.textContent).not.toContain("代码审计员");
    expect(
      screen.queryByTestId("status-strip-failed-detail-toggle"),
    ).toBeNull();
  });
});
