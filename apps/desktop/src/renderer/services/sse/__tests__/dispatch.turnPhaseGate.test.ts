import { logEvent } from "@/lib/log";
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import {
  beginTurnPreflight,
  enterTurnStreaming,
  useConversationStore,
} from "@/stores/conversation";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

vi.mock("@/services/api", () => ({
  api: { post: vi.fn() },
}));

vi.mock("@/services/turns/stopHydrate", () => ({
  armStopHydrateWatchdog: vi.fn(),
  clearStopHydrateWatchdog: vi.fn(),
  resetStopHydrateWatchdogForTests: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
  notifyWarning: vi.fn(),
  notifySuccess: vi.fn(),
}));

const CID = "conv-turn-phase-gate";
const logEventMock = vi.mocked(logEvent);

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: CID, byId: {} });
  useConversationStore.getState().switchConversation(CID);
  logEventMock.mockReset();
});

describe("dispatchSSEEvent turn-phase gate logging", () => {
  it("logs sse.event_dropped when content_delta is rejected in stopping", () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().stopGeneration();

    dispatchSSEEvent(
      {
        type: "content_delta",
        payload: { delta: "迟到正文" },
      } as never,
      { conversationId: CID, source: "server" },
    );

    expect(logEventMock).toHaveBeenCalledWith(
      "warn",
      "sse.event_dropped",
      expect.objectContaining({
        conversation_id: CID,
        event_type: "content_delta",
        turn_phase: "stopping",
        reason: "turn_phase_gate",
      }),
    );
  });

  it("ignores cloud conversation-SSE workspace_op_required (no turnPhase fail-settle)", () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().stopGeneration();

    dispatchSSEEvent(
      {
        type: "workspace_op_required",
        payload: {
          request_id: "r-gate",
          conversation_id: CID,
          root_id: "root-1",
          op: "read",
          args: { path: "a.txt" },
          timeout_ms: 5_000,
        },
        timestamp: "t0",
      } as never,
      { conversationId: CID, source: "server" },
    );

    expect(logEventMock).toHaveBeenCalledWith(
      "warn",
      "client_tool.ignored_on_conversation_sse",
      expect.objectContaining({
        conversation_id: CID,
        event_type: "workspace_op_required",
        reason: "fulfill_channel_owns_client_tool",
      }),
    );
    expect(logEventMock).not.toHaveBeenCalledWith(
      "warn",
      "workspace_op.dropped",
      expect.anything(),
    );
  });

  it("ignores cloud conversation-SSE host_op_required (no turnPhase fail-settle)", () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().stopGeneration();

    dispatchSSEEvent(
      {
        type: "host_op_required",
        payload: {
          request_id: "h-gate",
          conversation_id: CID,
          op: "host_shell",
          args: { command: "echo hi" },
        },
        timestamp: "t0",
      } as never,
      { conversationId: CID, source: "server" },
    );

    expect(logEventMock).toHaveBeenCalledWith(
      "warn",
      "client_tool.ignored_on_conversation_sse",
      expect.objectContaining({
        event_type: "host_op_required",
        reason: "fulfill_channel_owns_client_tool",
      }),
    );
    expect(logEventMock).not.toHaveBeenCalledWith(
      "warn",
      "host_op.dropped",
      expect.anything(),
    );
  });

  it("does not drop-log when checkpoint_required is allowed in terminal", () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().setTurnPhase("completed", CID);

    dispatchSSEEvent(
      {
        type: "checkpoint_required",
        payload: {
          checkpoint_id: "cp-gate",
          questions: [{ id: "q1", prompt: "拍板？" }],
        },
      } as never,
      { conversationId: CID, source: "server" },
    );

    expect(logEventMock).not.toHaveBeenCalledWith(
      "warn",
      "sse.event_dropped",
      expect.anything(),
    );
    expect(logEventMock).not.toHaveBeenCalledWith(
      "warn",
      "client_tool.ignored_on_conversation_sse",
      expect.anything(),
    );
  });
});
