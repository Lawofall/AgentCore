// @vitest-environment jsdom
/**
 * 主回复末尾费用：团队图与单聊同口径；具名恢复关 footer 时费用行仍可见。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { COST_UNPRICED_LABEL } from "@/lib/format";
import type { Message } from "@/stores/conversation";
import type { Execution, RunNode } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const execById = vi.hoisted(() => ({
  value: {} as Record<string, { deliveryStatus: null; plan?: unknown }>,
}));
const mockExecution = vi.hoisted(() => ({
  value: null as Execution | null,
}));
const messageCosts = vi.hoisted(() => ({
  value: {} as Record<string, never>,
}));

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    useConversationStore: (
      sel: (s: { currentConversationId: string | null }) => unknown,
    ) => sel({ currentConversationId: "conv-1" }),
    getActiveRuntime: () => ({ messages: [] }),
    assistantProjectionId: (m: { id: string }) => m.id,
  };
});

vi.mock("@/stores/usage", () => ({
  useUsageStore: (
    sel: (s: {
      loadMessageCost: () => void;
      messageCosts: Record<string, never>;
    }) => unknown,
  ) =>
    sel({
      loadMessageCost: () => {},
      messageCosts: messageCosts.value,
    }),
}));

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useExecutionStore: (
      sel: (s: {
        byId: Record<string, { deliveryStatus: null; plan?: unknown }>;
      }) => unknown,
    ) => sel({ byId: execById.value }),
    useMessageExecution: (messageId: string | null) =>
      messageId ? mockExecution.value : null,
  };
});

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: [],
      planReviews: [],
    }),
  };
});

vi.mock("@/stores/bookmarks", () => ({
  useBookmarkStore: (
    sel: (s: { ids: Set<string>; toggle: () => void }) => unknown,
  ) => sel({ ids: new Set(), toggle: () => {} }),
}));

vi.mock("@/services/messages", () => ({
  setMessageFeedback: vi.fn(),
}));

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

vi.mock("@/components/chat/debate/CollapsibleSpeech", () => ({
  CollapsibleSpeech: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

import { AssistantMessage } from "../AssistantMessage";

function settledMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "已完成的回复",
    createdAt: "2026-08-05T00:00:00Z",
    executionId: null,
    isStreaming: false,
    ...overrides,
  };
}

function renderBubble(message: Message) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AssistantMessage message={message} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

function nanoCost(
  total: number,
  extra: Partial<NonNullable<RunNode["cost"]>> = {},
): NonNullable<RunNode["cost"]> {
  return { input: 0, cached: 0, output: 0, total, currency: "CNY", ...extra };
}

function runNode(
  over: Partial<RunNode> & Pick<RunNode, "id" | "agentId" | "task" | "status">,
): RunNode {
  return {
    dependsOn: [],
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    error: null,
    parentRunId: null,
    kind: "agent",
    role: null,
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
    ...over,
  };
}

function teamExecution(runs: Execution["runs"]): Execution {
  return {
    id: "exec-1",
    planType: "multi_agent",
    taskSummary: "团队任务",
    status: "completed",
    agents: [],
    runs,
    acts: [],
    progress: { completed: runs.length, total: runs.length },
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
    evidenceLedger: [],
  };
}

afterEach(() => {
  cleanup();
  execById.value = {};
  mockExecution.value = null;
  messageCosts.value = {};
});

describe("AssistantMessage turn cost at bubble end", () => {
  it("单聊：message.cost 展示在 footer 末尾", () => {
    renderBubble(
      settledMessage({
        cost: nanoCost(5_000_000_000),
      }),
    );
    expect(screen.getByText("¥5.00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制" })).toBeTruthy();
  });

  it("团队图：按本条 execution runs 之和展示总价", () => {
    execById.value = {
      "asst-1": { deliveryStatus: null, plan: { agents: [] } },
    };
    mockExecution.value = teamExecution([
      runNode({
        id: "r1",
        agentId: "a1",
        task: "调研",
        status: "completed",
        cost: nanoCost(5_000_000_000),
        usage: {
          input: 100,
          output: 50,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
        },
      }),
      runNode({
        id: "r2",
        agentId: "a2",
        task: "撰写",
        status: "completed",
        dependsOn: ["r1"],
        cost: nanoCost(3_000_000_000),
        usage: {
          input: 80,
          output: 40,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
        },
      }),
    ]);
    renderBubble(
      settledMessage({
        executionId: "exec-1",
        content: "团队汇总答复",
      }),
    );
    expect(screen.getByText("¥8.00")).toBeTruthy();
  });

  it("团队图未计价：展示未计价标注", () => {
    execById.value = {
      "asst-1": { deliveryStatus: null, plan: { agents: [] } },
    };
    mockExecution.value = teamExecution([
      runNode({
        id: "r1",
        agentId: "a1",
        task: "调研",
        status: "completed",
        cost: nanoCost(0, { pricing_source: "unpriced", currency: "USD" }),
        usage: {
          input: 120,
          output: 60,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
        },
      }),
    ]);
    renderBubble(
      settledMessage({
        executionId: "exec-1",
        content: "BYOK 团队回合",
      }),
    );
    expect(screen.getByText(COST_UNPRICED_LABEL)).toBeTruthy();
  });

  it("具名恢复关 footer 时费用行仍在主回复末尾", () => {
    execById.value = {
      "asst-1": { deliveryStatus: null, plan: { agents: [] } },
    };
    mockExecution.value = teamExecution([
      runNode({
        id: "r1",
        agentId: "a1",
        task: "调研",
        status: "failed",
        cost: nanoCost(4_000_000_000),
        usage: {
          input: 50,
          output: 20,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
        },
      }),
    ]);
    renderBubble(
      settledMessage({
        executionId: "exec-1",
        content: "团队已产出部分正文",
        finishReason: "error",
        error: {
          code: "LLM_KEY_INVALID",
          message:
            "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
          context: { credential_source: "platform" },
        },
      }),
    );
    expect(screen.getByText("¥4.00")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
    expect(screen.queryByRole("button", { name: "复制" })).toBeNull();
  });
});
