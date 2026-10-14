// @vitest-environment jsdom
/**
 * 同人续写座位：右坞在原节点往下接，不靠「现场 | 续 ×1」横轨切人。
 */
import { RunDetailBody } from "@/components/chat/detail/RunDetailBody";
import type { AgentState, Execution, RunNode } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

let mockExecution: Execution | null = null;

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useMessageRun: (_messageId: string, runId: string) => {
      if (!mockExecution) return null;
      const run = mockExecution.runs.find((r) => r.id === runId);
      const agent = run
        ? mockExecution.agents.find((a) => a.id === run.agentId)
        : null;
      if (!run || !agent) return null;
      return { execution: mockExecution, run, agent };
    },
  };
});

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      currentConversationId: "c1",
      messages: [{ id: "m1", isStreaming: false, collab: null, traceId: null }],
    }),
  activeRuntime: (s: { messages: unknown[] }) => s,
  runtimeOf: () => ({ toolStartedMs: {} }),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ showRunDetail: vi.fn() }),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

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

afterEach(() => {
  cleanup();
  mockExecution = null;
});

function agent(
  partial: Partial<AgentState> & Pick<AgentState, "id" | "role">,
): AgentState {
  return {
    thinking: false,
    status: "completed",
    currentRunId: null,
    outputChunks: [],
    reasoningChunks: [],
    toolCalls: [],
    toolProgress: null,
    toolExecutionLive: null,
    ...partial,
  };
}

function continuationCtx(body: string): RunNode["receivedContext"] {
  return [
    {
      channel: "continuation",
      heading: "continuation",
      body,
      chars: body.length,
      truncated: false,
      source_role: "",
      source_run_id: "",
      fidelity: "",
      files: [],
    },
  ];
}

function run(
  partial: Partial<RunNode> & Pick<RunNode, "id" | "agentId" | "status">,
): RunNode {
  return {
    task: "执行一次约 60 秒的等待",
    dependsOn: [],
    parentRunId: null,
    kind: "agent",
    role: "计时测试员",
    model: null,
    reasoningEffort: null,
    usage: null,
    cost: null,
    error: null,
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    continuationIndex: 0,
    replacesRunId: null,
    revised: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    sideKey: null,
    ...partial,
  };
}

describe("RunDetailBody seat continuation", () => {
  it("stacks prior attempt below the original task and shows 按指示 on the tail", () => {
    mockExecution = {
      id: "e1",
      planType: "multi_agent",
      taskSummary: "计时",
      status: "completed",
      agents: [
        agent({ id: "a1", role: "计时测试员", outputChunks: ["第一截"] }),
        agent({ id: "a2", role: "计时测试员", outputChunks: ["第二截"] }),
      ],
      runs: [
        run({
          id: "r1",
          agentId: "a1",
          status: "cancelled",
          outputSummary: "半成品",
          process: [{ kind: "content", text: "第一截过程" }],
        }),
        run({
          id: "r1b",
          agentId: "a2",
          status: "completed",
          continuesRunId: "r1",
          continuationIndex: 1,
          receivedContext: continuationCtx("取消"),
          outputSummary: "已按新指示做完",
          process: [{ kind: "content", text: "第二截过程" }],
        }),
      ],
      acts: [],
      progress: { completed: 1, total: 2 },
      batches: [],
      debate: null,
      debateRounds: [],
      crossExamEnabled: false,
      debateOpening: null,
    };

    render(
      <MemoryRouter>
        <RunDetailBody messageId="m1" runId="r1" />
      </MemoryRouter>,
    );

    expect(screen.getByText("执行一次约 60 秒的等待")).toBeTruthy();
    expect(screen.getByText("按指示：取消")).toBeTruthy();
    expect(screen.getByText("第二截过程")).toBeTruthy();
    expect(screen.getByText("已按新指示做完")).toBeTruthy();
    expect(screen.queryByText("接续")).toBeNull();
    expect(screen.queryByText("续 ×1")).toBeNull();
  });
});
