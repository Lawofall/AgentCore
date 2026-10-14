import { useConversationStore } from "@/stores/conversation";
import { enterTurnStreaming } from "@/stores/conversation/turnPhaseActions";
import { useExecutionStore } from "@/stores/execution";
import { beforeEach, describe, expect, it } from "vitest";
import { dispatchSSEEvent } from "../dispatch";
import { execMessageId } from "../helpers";

const CONV = "conv-graph-append-route";

function seedAssistant(id: string, extras: Record<string, unknown> = {}) {
  useConversationStore.getState().addMessage(
    {
      id,
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      executionId: null,
      isStreaming: true,
      serverMessageId: id,
      ...extras,
    },
    CONV,
  );
}

beforeEach(() => {
  useConversationStore.getState().dropConversationRuntime(CONV);
  useExecutionStore.setState({ byId: {} });
  useConversationStore.getState().switchConversation(CONV);
  enterTurnStreaming(CONV);
});

describe("execMessageId graph routing", () => {
  it("routes host_message_id hint to the host slot (detached / old journal)", () => {
    seedAssistant("m1", { executionId: "exec1" });
    seedAssistant("m2");
    expect(
      execMessageId(CONV, { host_message_id: "m1", execution_id: "exec1" }),
    ).toBe("m1");
  });

  it("same execution_id merges onto the existing host slot (no sticky divert)", () => {
    seedAssistant("m1", { executionId: "exec1" });
    seedAssistant("m2");
    // Without sticky divert, same-id lookup still finds m1.
    expect(execMessageId(CONV, { execution_id: "exec1" })).toBe("m1");
    // Unrelated / unknown id falls through to latest assistant.
    expect(execMessageId(CONV, { execution_id: "exec-new" })).toBe("m2");
    expect(execMessageId(CONV)).toBe("m2");
  });

  it("live: new turn with prev_execution_id opens its own graph on m2", () => {
    // Turn 1 — build graph on m1
    dispatchSSEEvent(
      {
        type: "message_start",
        payload: { message_id: "m1", conversation_id: CONV },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_plan",
        payload: {
          execution_id: "exec1",
          plan_type: "multi_agent",
          task_summary: "调研",
          agents: [
            {
              id: "w1",
              role: "研究员",
              thinking: true,
            },
          ],
          runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_started",
        payload: {
          run_id: "r1",
          agent_id: "w1",
          parent_run_id: null,
          kind: "agent",
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_completed",
        payload: {
          run_id: "r1",
          agent_id: "w1",
          output_summary: "done",
          duration_ms: 10,
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "message_end",
        payload: { finish_reason: "end_turn" },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );

    expect(useExecutionStore.getState().byId.m1?.status).toBe("completed");
    expect(useExecutionStore.getState().byId.m1?.plan?.id).toBe("exec1");

    useConversationStore.getState().setTurnPhase("streaming", CONV);

    // Turn 2 — new execution + prev link (no graph_append / host_message_id)
    dispatchSSEEvent(
      {
        type: "message_start",
        payload: { message_id: "m2", conversation_id: CONV },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_plan",
        payload: {
          execution_id: "exec2",
          plan_type: "multi_agent",
          task_summary: "撰写",
          prev_execution_id: "exec1",
          agents: [
            {
              id: "w3",
              role: "撰写员",
              thinking: true,
            },
          ],
          runs: [{ id: "r3", agent_id: "w3", task: "撰写", depends_on: [] }],
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );

    const m2 = useConversationStore
      .getState()
      .byId[CONV].messages.find((m) => m.serverMessageId === "m2");
    expect(m2?.process?.some((s) => s.kind === "team")).toBe(true);
    expect(m2?.executionId).toBe("exec2");
    expect(useExecutionStore.getState().byId.m2?.plan?.id).toBe("exec2");
    expect(useExecutionStore.getState().byId.m2?.plan?.prevExecutionId).toBe(
      "exec1",
    );
    // Prior graph untouched.
    expect(useExecutionStore.getState().byId.m1?.plan?.id).toBe("exec1");
    expect(useExecutionStore.getState().byId.m1?.status).toBe("completed");

    dispatchSSEEvent(
      {
        type: "run_started",
        payload: {
          run_id: "r3",
          agent_id: "w3",
          parent_run_id: null,
          kind: "agent",
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_completed",
        payload: {
          run_id: "r3",
          agent_id: "w3",
          output_summary: "写完",
          duration_ms: 10,
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );

    expect(useExecutionStore.getState().byId.m2?.status).toBe("completed");
    expect(useExecutionStore.getState().byId.m1?.status).toBe("completed");

    dispatchSSEEvent(
      {
        type: "message_end",
        payload: { finish_reason: "end_turn" },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
  });

  it("host_message_id does not suppress the team marker on the current turn", () => {
    dispatchSSEEvent(
      {
        type: "message_start",
        payload: { message_id: "m1", conversation_id: CONV },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_plan",
        payload: {
          execution_id: "exec1",
          plan_type: "multi_agent",
          task_summary: "调研",
          agents: [{ id: "w1", role: "研究员", thinking: true }],
          runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_started",
        payload: {
          run_id: "r1",
          agent_id: "w1",
          parent_run_id: null,
          kind: "agent",
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "run_completed",
        payload: {
          run_id: "r1",
          agent_id: "w1",
          output_summary: "done",
          duration_ms: 10,
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "message_end",
        payload: { finish_reason: "end_turn" },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );

    useConversationStore.getState().setTurnPhase("streaming", CONV);

    dispatchSSEEvent(
      {
        type: "message_start",
        payload: { message_id: "m2", conversation_id: CONV },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );

    dispatchSSEEvent(
      {
        type: "run_plan",
        payload: {
          execution_id: "exec1",
          plan_type: "multi_agent",
          task_summary: "调研撰写",
          host_message_id: "m1",
          agents: [
            { id: "w1", role: "研究员", thinking: true },
            { id: "w3", role: "撰写员", thinking: true },
          ],
          runs: [
            { id: "r1", agent_id: "w1", task: "调研", depends_on: [] },
            { id: "r3", agent_id: "w3", task: "撰写", depends_on: [] },
          ],
        },
        timestamp: "",
      },
      { conversationId: CONV, source: "server" },
    );

    // 同一张执行单仍合进已有槽；旧字段不再挡住当前这轮的图标记。
    const m2 = useConversationStore
      .getState()
      .byId[CONV].messages.find((m) => m.serverMessageId === "m2");
    expect(useExecutionStore.getState().byId.m1?.plan?.runs.length).toBe(2);
    expect(useExecutionStore.getState().byId.m2?.plan).toBeFalsy();
    expect(m2?.process?.some((s) => s.kind === "team")).toBe(true);
  });
});
