// @vitest-environment jsdom
/**
 * FailureStrip error detail (91eb)：execution=failed 但无 failedRun 时，
 * 回退会话级 error（与底栏 RetryBanner 同源），禁止仍写「未获取到具体错误信息。」
 *
 * 有 failedRun 时改按 failureKind 出人话——`run.error` 是模型面（基础设施路径上就是
 * `str(exception)`，契约路径上是「缺少必备章节：…」这类引擎词），照抄给用户会让他以为是自己
 * 少放了材料。
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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

describe("StatusStrip · FailureStrip error detail", () => {
  it("no failedRun.error + session interrupt → show session copy, not 未获取到", () => {
    sessionError = INTERRUPT_COPY;
    const exec = projectExecution(plan, completedOnlyFrames, "failed");
    expect(exec.status).toBe("failed");
    expect(exec.runs.find((r) => r.status === "failed")).toBeUndefined();

    renderStrip(exec);

    expect(screen.getByTestId("status-strip-failed")).toBeTruthy();
    expect(screen.getByText(INTERRUPT_COPY)).toBeTruthy();
    expect(screen.queryByText("未获取到具体错误信息。")).toBeNull();
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
  });

  it("failed run → curated sentence, never the raw run.error", () => {
    sessionError = INTERRUPT_COPY;
    const exec = projectExecution(plan, failedWithErrorFrames, "failed");
    const failed = exec.runs.find((r) => r.status === "failed");
    expect(failed?.error).toBe("工具超时：web_search");

    renderStrip(exec);

    expect(screen.queryByText("工具超时：web_search")).toBeNull();
    expect(screen.getByText(failureDetailSentence(null, null))).toBeTruthy();
    expect(screen.queryByText(INTERRUPT_COPY)).toBeNull();
    expect(screen.queryByText("未获取到具体错误信息。")).toBeNull();
  });

  it("contract gate error never reaches the user as engine jargon", () => {
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

    // The user must not read engine chapter-gate jargon / an artifact path and conclude they forgot to
    // hand something in — that reason is ours to act on, not theirs.
    expect(container.textContent).not.toContain("缺少必备章节");
    expect(container.textContent).not.toContain(".audit.json");
    expect(
      screen.getByText(failureDetailSentence("format", null)),
    ).toBeTruthy();
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
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
  });

  it("no run error and no session error → keep 未获取到 fallback", () => {
    sessionError = null;
    const exec = projectExecution(plan, completedOnlyFrames, "failed");

    renderStrip(exec);

    expect(screen.getByText("未获取到具体错误信息。")).toBeTruthy();
  });

  it("long failed task brief defaults to clamped toggle (does not dump full brief)", async () => {
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
    renderStrip(exec);

    const toggle = screen.getByTestId("status-strip-failed-detail-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Collapsed: clamp classes present; full brief not forced open.
    const taskLine = toggle.querySelector("p");
    expect(taskLine?.className).toContain("line-clamp-2");
    expect(taskLine?.className).not.toContain("whitespace-pre-wrap");

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.querySelector("p")?.className).toContain(
      "whitespace-pre-wrap",
    );
  });
});
