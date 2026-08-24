import { useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();
vi.mock("@/services/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

import {
  ensureFullMessageRuns,
  resetEnsureFullMessageRunsForTests,
} from "../messages";

const CID = "conv-full";
const MID = "msg-full";

function slimRow() {
  return {
    id: MID,
    conversation_id: CID,
    role: "assistant" as const,
    content: "结论",
    reasoning_content: null,
    created_at: "2026-01-01T00:00:00Z",
    status: "complete",
    runs: {
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00.000Z",
          payload: {
            execution_id: "exec-full",
            plan_type: "multi_agent",
            task_summary: "调研",
            agents: [{ id: "a1", role: "研究员" }],
            runs: [{ id: "r1", agent_id: "a1", task: "读", depends_on: [] }],
          },
        },
      ],
      finish_reason: "stop",
      events_complete: false,
    },
  };
}

function fullRow() {
  const slim = slimRow();
  return {
    ...slim,
    runs: {
      ...slim.runs,
      events: [
        ...slim.runs.events,
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: { run_id: "r1", agent_id: "a1" },
        },
      ],
      events_complete: true,
    },
  };
}

beforeEach(() => {
  resetEnsureFullMessageRunsForTests();
  apiGet.mockReset();
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
    pendingFocus: null,
  });
});

describe("ensureFullMessageRuns", () => {
  it("fetches GET one message, writes runs, hydrates; second call is a no-op", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage({
      id: MID,
      role: "assistant",
      content: "结论",
      createdAt: "2026-01-01T00:00:00Z",
      executionId: "exec-full",
      isStreaming: false,
      runs: {
        events: slimRow().runs.events as never,
        finishReason: "stop",
        eventsComplete: false,
      },
    });
    apiGet.mockResolvedValue(fullRow());

    const got = await ensureFullMessageRuns(CID, MID);
    expect(got?.runs?.eventsComplete).not.toBe(false);
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet.mock.calls[0]?.[0]).toBe(
      `/v1/conversations/${CID}/messages/${MID}`,
    );
    expect(
      useConversationStore.getState().byId[CID]?.messages[0]?.runs?.events
        .length,
    ).toBe(2);
    expect(useExecutionStore.getState().byId[MID]?.plan?.id).toBe("exec-full");

    await ensureFullMessageRuns(CID, MID);
    expect(apiGet).toHaveBeenCalledTimes(1);
  });

  it("coalesces in-flight GETs for the same message", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage({
      id: MID,
      role: "assistant",
      content: "结论",
      createdAt: "",
      executionId: "exec-full",
      isStreaming: false,
      runs: {
        events: slimRow().runs.events as never,
        finishReason: "stop",
        eventsComplete: false,
      },
    });
    let resolveGet: (value: unknown) => void = () => {};
    apiGet.mockReturnValue(
      new Promise((resolve) => {
        resolveGet = resolve;
      }),
    );

    const p1 = ensureFullMessageRuns(CID, MID);
    const p2 = ensureFullMessageRuns(CID, MID);
    expect(apiGet).toHaveBeenCalledTimes(1);
    resolveGet(fullRow());
    await Promise.all([p1, p2]);
    expect(apiGet).toHaveBeenCalledTimes(1);
  });
});
