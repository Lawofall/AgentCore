// @vitest-environment jsdom
/**
 * RunDetailBody hybrid layout: ProcessTimeline body (not partitioned 思考/工具/输出).
 */
import { RunDetailBody } from "@/components/chat/detail/RunDetailBody";
import type { AgentState, Execution, RunNode } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

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
      messages: [{ id: "m1", isStreaming: false, collab: null, traceId: null }],
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

afterEach(cleanup);

const agent: AgentState = {
  id: "w1",
  role: "调研员",
  thinking: true,
  status: "completed",
  currentRunId: null,
  outputChunks: ["最终建议：跟价。"],
  reasoningChunks: ["先搜一圈。"],
  toolCalls: [
    {
      id: "tc1",
      toolName: "web_search",
      arguments: { query: "竞品定价" },
      result: "命中 3 条",
      status: "success",
    },
  ],
  toolProgress: null,
  toolExecutionLive: null,
};

const run: RunNode = {
  id: "r1",
  agentId: "w1",
  task: "调研竞品",
  status: "completed",
  dependsOn: [],
  outputSummary: "完成调研",
  outputFiles: [],
  debrief: null,
  durationMs: 2000,
  startedAt: null,
  error: null,
  parentRunId: null,
  kind: "agent",
  role: "member",
  model: "deepseek-v4-flash",
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
  process: [
    { kind: "reasoning", text: "先搜一圈。" },
    {
      kind: "tool",
      id: "tc1",
      tool_name: "web_search",
      arguments: { query: "竞品定价" },
      result: "命中 3 条",
      status: "success",
    },
    { kind: "content", text: "初步结论：价格带偏高。" },
    { kind: "reasoning", text: "再读一篇。" },
    {
      kind: "tool",
      id: "tc2",
      tool_name: "read_url",
      arguments: { url: "https://example.com" },
      result: "正文…",
      status: "success",
    },
    { kind: "content", text: " 最终建议：跟价。" },
  ],
};

const mockExecution: Execution = {
  id: "exec1",
  planType: "multi_agent",
  taskSummary: "调研竞品",
  status: "completed",
  agents: [agent],
  runs: [run],
  acts: [
    {
      actId: "act-1",
      kind: "multi_agent",
      title: null,
      anchorRunId: null,
      authorizedBy: null,
    },
  ],
  progress: { completed: 1, total: 1 },
  batches: [],
  debate: null,
  debateRounds: [],
  crossExamEnabled: false,
  debateOpening: null,
  debatePretrial: null,
};

const HANDOFF_RECEIPT = "已收尾。";

const handoffStep = {
  kind: "tool" as const,
  id: "h1",
  tool_name: "handoff",
  arguments: {
    summary: "交叉验证完成",
    key_points: ["共识：一周内需清晰立场"],
  },
  result: HANDOFF_RECEIPT,
  status: "success" as const,
};

describe("RunDetailBody process timeline", () => {
  it("hides footer 交接简报 when process already has a successful handoff", () => {
    const prevDebrief = run.debrief;
    const prevProcess = run.process;
    run.debrief = {
      summary: "交叉验证完成",
      key_points: ["共识：一周内需清晰立场"],
    };
    run.process = [{ kind: "reasoning", text: "收尾。" }, handoffStep];
    try {
      render(
        <MemoryRouter>
          <RunDetailBody messageId="m1" runId="r1" />
        </MemoryRouter>,
      );
      expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
      expect(screen.queryByText("交叉验证完成")).toBeNull();
      expect(screen.queryByText("Handoff")).toBeNull();
      expect(screen.queryByText("简报由系统降级生成")).toBeNull();
    } finally {
      run.debrief = prevDebrief;
      run.process = prevProcess;
    }
  });

  it("keeps footer degraded debrief when process has no successful handoff", () => {
    const prevDebrief = run.debrief;
    const prevProcess = run.process;
    run.debrief = {
      summary: "正文切片当简报",
      degraded: true,
    } as typeof run.debrief & { degraded: true };
    run.process = [
      { kind: "reasoning", text: "没打到 handoff。" },
      {
        kind: "tool",
        id: "tc1",
        tool_name: "web_search",
        arguments: { query: "竞品定价" },
        result: "命中 3 条",
        status: "success",
      },
    ];
    try {
      render(
        <MemoryRouter>
          <RunDetailBody messageId="m1" runId="r1" />
        </MemoryRouter>,
      );
      expect(screen.getByText("交接简报")).toBeTruthy();
      expect(screen.getByText("简报由系统降级生成")).toBeTruthy();
      expect(screen.queryByText("正文切片当简报")).toBeNull();
    } finally {
      run.debrief = prevDebrief;
      run.process = prevProcess;
    }
  });

  it("renders interleaved ProcessTimeline instead of partitioned 思考/工具/输出", () => {
    render(
      <MemoryRouter>
        <RunDetailBody messageId="m1" runId="r1" />
      </MemoryRouter>,
    );

    expect(screen.getByText("调研员")).toBeTruthy();
    expect(screen.getByText("调研竞品")).toBeTruthy();
    // Timeline body: reasoning headers + content + tool labels (CEO ProcessTimeline).
    expect(screen.getAllByText("Thought").length).toBeGreaterThan(0);
    expect(screen.getByText(/初步结论/)).toBeTruthy();
    expect(screen.getByText(/最终建议/)).toBeTruthy();
    // Footer conclusion kept.
    expect(screen.getByText("结论")).toBeTruthy();
    expect(screen.getByText("完成调研")).toBeTruthy();
    // Old partitioned section titles must not appear as standalone section chrome.
    expect(screen.queryByText("输出")).toBeNull();
    expect(screen.queryByText("工具调用")).toBeNull();
  });
});
