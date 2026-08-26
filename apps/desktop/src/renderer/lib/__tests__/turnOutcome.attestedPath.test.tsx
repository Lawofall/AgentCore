// @vitest-environment jsdom
/**
 * Production path: `message_end.outcome` must reach the product arbitrator
 * (`attestedKind`) via live SSE, REST hydrate, and journal reload — not only
 * the pure `arbitrateTurnOutcome({ attestedKind })` helper.
 *
 * Discriminator: local bits would paint `error` (empty + finish_reason=error,
 * no delivery.partial, no productLanded). Server attests `partial` → UI kind
 * must be partial.
 */
import { StatusStrip } from "@/components/chat/StatusStrip";
import { TooltipProvider } from "@/components/ui/tooltip";
import { conversationKeys } from "@/lib/queryKeys";
import { turnOutcomeForAssistant } from "@/lib/turnOutcome";
import { type BackendMessage, toMessage } from "@/services/messages";
import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import {
  getRuntime,
  getTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import {
  type ExecutionPlan,
  ExecutionScopeContext,
  execRuntime,
  projectExecution,
  useExecutionStore,
} from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CID = "conv-attested-outcome";
const MID = "msg-attested-outcome";

const plan: ExecutionPlan = {
  id: "exec-attested",
  planType: "multi_agent",
  taskSummary: "导出 CSV",
  agents: [{ id: "w1", role: "数据分析" }],
  runs: [{ id: "r1", agentId: "w1", task: "导出", dependsOn: [] }],
};

const failedNoProductFrames = [
  {
    t: 1,
    kind: "run_started" as const,
    runId: "r1",
    agentId: "w1",
    parentRunId: null,
    runKind: "agent" as const,
    continuesRunId: null,
  },
  {
    t: 2,
    kind: "run_failed" as const,
    runId: "r1",
    agentId: "w1",
    error: "上游限流",
    failureKind: "call" as const,
  },
];

function row(
  over: Partial<BackendMessage> & Pick<BackendMessage, "id" | "role">,
): BackendMessage {
  return {
    conversation_id: CID,
    content: "",
    reasoning_content: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

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

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
});

afterEach(() => {
  cleanup();
});

describe("attested outcome production path", () => {
  it("live message_end stamps store + arbitrator (local bits would be error)", () => {
    const conv = useConversationStore.getState();
    conv.addMessage({
      id: "u1",
      role: "user",
      content: "go",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    conv.createAssistantMessage(CID);
    conv.setServerMessageIdOnLastMessage(MID, CID);
    useExecutionStore.getState().startExecution(plan, MID);

    handleMessageStreamEvent(
      {
        type: "message_end",
        timestamp: "2026-01-01T00:00:00Z",
        payload: { finish_reason: "error", outcome: "partial" },
      },
      { conversationId: CID, source: "server" },
    );

    const msg = getRuntime(CID).messages.at(-1);
    expect(msg?.outcome).toBe("partial");
    expect(useExecutionStore.getState().byId[MID]?.attestedOutcome).toBe(
      "partial",
    );

    const slot = execRuntime(useExecutionStore.getState(), MID);
    if (!msg) throw new Error("expected last message");
    const outcome = turnOutcomeForAssistant(msg, slot);
    expect(outcome.kind).toBe("partial");
    expect(outcome.kind).not.toBe("error");
  });

  it("REST toMessage hydrates outcome onto the product arbitrator", () => {
    const msg = toMessage(
      row({
        id: MID,
        role: "assistant",
        content: "",
        outcome: "partial",
        runs: { events: [], finish_reason: "error" },
      }),
    );
    expect(msg.outcome).toBe("partial");
    expect(turnOutcomeForAssistant(msg, null).kind).toBe("partial");
  });

  it("journal message_end.outcome hydrates the slot when REST omits it", () => {
    const msg = toMessage(
      row({
        id: `${MID}-journal`,
        role: "assistant",
        content: "",
        outcome: null,
        runs: {
          finish_reason: "error",
          events: [
            {
              type: "message_end",
              timestamp: "2026-01-01T00:00:00Z",
              payload: { finish_reason: "error", outcome: "partial" },
            },
          ],
        },
      }),
    );
    expect(msg.outcome).toBe("partial");
    expect(turnOutcomeForAssistant(msg, null).kind).toBe("partial");
  });

  it("live message_end outcome=paused stamps continue, not error", () => {
    const conv = useConversationStore.getState();
    conv.addMessage({
      id: "u1",
      role: "user",
      content: "go",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    conv.createAssistantMessage(CID);
    conv.setServerMessageIdOnLastMessage(MID, CID);
    useExecutionStore.getState().startExecution(plan, MID);

    handleMessageStreamEvent(
      {
        type: "error",
        timestamp: "2026-01-01T00:00:00Z",
        payload: {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
        },
      },
      { conversationId: CID, source: "server" },
    );
    expect(useExecutionStore.getState().byId[MID]?.status).toBe("failed");

    handleMessageStreamEvent(
      {
        type: "message_end",
        timestamp: "2026-01-01T00:00:01Z",
        payload: { finish_reason: "paused", outcome: "paused" },
      },
      { conversationId: CID, source: "server" },
    );

    const msg = getRuntime(CID).messages.at(-1);
    expect(msg?.outcome).toBe("paused");
    expect(useExecutionStore.getState().byId[MID]?.attestedOutcome).toBe(
      "paused",
    );
    expect(useExecutionStore.getState().byId[MID]?.status).toBe("paused");
    expect(getTurnPhase(CID)).toBe("completed");

    const slot = execRuntime(useExecutionStore.getState(), MID);
    if (!msg) throw new Error("expected last message");
    const outcome = turnOutcomeForAssistant(msg, slot);
    expect(outcome.kind).toBe("paused");
    expect(outcome.recovery.kind).toBe("continue");
    expect(outcome.showBubbleBanner).toBe(false);
    expect(outcome.showComposerHint).toBe(false);
  });

  it("StatusStrip consumes hydrated attestedOutcome without local partial bits", () => {
    useExecutionStore.getState().hydrateFromJournal(MID, {
      finishReason: "error",
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00Z",
          payload: {
            execution_id: plan.id,
            plan_type: "multi_agent",
            task_summary: plan.taskSummary,
            agents: plan.agents,
            runs: [
              {
                id: "r1",
                agent_id: "w1",
                task: "导出",
                depends_on: [],
              },
            ],
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01Z",
          payload: {
            agent_id: "w1",
            run_id: "r1",
            parent_run_id: null,
            kind: "agent",
          },
        },
        {
          type: "run_failed",
          timestamp: "2026-01-01T00:00:02Z",
          payload: {
            run_id: "r1",
            agent_id: "w1",
            error: "上游限流",
            failure_kind: "call",
          },
        },
        {
          type: "message_end",
          timestamp: "2026-01-01T00:00:03Z",
          payload: { finish_reason: "error", outcome: "partial" },
        },
      ],
    });

    expect(execRuntime(useExecutionStore.getState(), MID).attestedOutcome).toBe(
      "partial",
    );

    const exec = projectExecution(plan, failedNoProductFrames, "failed");
    renderStrip(exec);
    expect(screen.getByTestId("status-strip-partial")).toBeTruthy();
    expect(screen.getByText("部分完成")).toBeTruthy();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
  });

  it("StatusStrip consumes attested paused as 已暂停+继续, not 失败", () => {
    useExecutionStore.getState().hydrateFromJournal(MID, {
      finishReason: "paused",
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00Z",
          payload: {
            execution_id: plan.id,
            plan_type: "multi_agent",
            task_summary: plan.taskSummary,
            agents: plan.agents,
            runs: [
              {
                id: "r1",
                agent_id: "w1",
                task: "导出",
                depends_on: [],
              },
            ],
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01Z",
          payload: {
            agent_id: "w1",
            run_id: "r1",
            parent_run_id: null,
            kind: "agent",
          },
        },
        {
          type: "run_failed",
          timestamp: "2026-01-01T00:00:02Z",
          payload: {
            run_id: "r1",
            agent_id: "w1",
            error: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
            error_code: "LLM_RATE_LIMIT",
            failure_kind: "call",
            product_landed: true,
            retryable: true,
          },
        },
        {
          type: "message_end",
          timestamp: "2026-01-01T00:00:03Z",
          payload: { finish_reason: "paused", outcome: "paused" },
        },
      ],
    });

    expect(execRuntime(useExecutionStore.getState(), MID).attestedOutcome).toBe(
      "paused",
    );
    expect(execRuntime(useExecutionStore.getState(), MID).status).toBe(
      "paused",
    );

    const exec = projectExecution(plan, failedNoProductFrames, "failed");
    renderStrip(exec);
    expect(screen.getByTestId("status-strip-paused")).toBeTruthy();
    expect(screen.getByTestId("paused-continue-action")).toBeTruthy();
    expect(screen.getByText("已暂停")).toBeTruthy();
    expect(screen.queryByTestId("status-strip-failed")).toBeNull();
    expect(screen.queryByTestId("status-strip-partial")).toBeNull();
  });
});
