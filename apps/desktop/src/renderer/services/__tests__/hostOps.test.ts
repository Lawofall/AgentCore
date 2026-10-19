import { beforeEach, describe, expect, it, vi } from "vitest";

const resolveInteraction = vi.fn().mockResolvedValue(undefined);
const logEvent = vi.fn();

vi.mock("@/services/interaction", () => ({
  resolveInteraction: (...args: unknown[]) => resolveInteraction(...args),
}));

vi.mock("@/lib/log", () => ({
  logEvent: (...args: unknown[]) => logEvent(...args),
}));

import { dispatchSSEEvent } from "@/services/sse/dispatch";
import { useConversationStore } from "@/stores/conversation/store";
import type { HostOpRequiredPayload } from "@/types/events";
import { resetClientToolFulfillmentForTests } from "../clientToolFulfill";
import { performHostOp } from "../hostOps";

const CID = "conv-1";

function payload(
  over: Partial<HostOpRequiredPayload> = {},
): HostOpRequiredPayload {
  return {
    request_id: "host-1",
    conversation_id: CID,
    op: "shell",
    args: { command: "echo hi" },
    ...over,
  };
}

describe("performHostOp", () => {
  beforeEach(() => {
    resetClientToolFulfillmentForTests();
    resolveInteraction.mockClear();
    logEvent.mockClear();
    vi.stubGlobal("window", {
      hostApi: {
        runOp: vi.fn().mockResolvedValue({ ok: true, value: { code: 0 } }),
      },
    });
  });

  it("runs host op and posts client_tool result", async () => {
    await performHostOp(payload(), CID, "cloud");
    expect(window.hostApi?.runOp).toHaveBeenCalledWith({
      op: "shell",
      args: { command: "echo hi" },
    });
    expect(resolveInteraction).toHaveBeenCalledWith(
      CID,
      "host-1",
      expect.objectContaining({
        kind: "client_tool",
        ok: true,
        value: { code: 0 },
      }),
      "cloud",
    );
  });

  it("does not re-run host side effect on the same request_id", async () => {
    await performHostOp(payload(), CID, "cloud");
    await performHostOp(payload(), CID, "cloud");
    expect(window.hostApi?.runOp).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(1);
  });

  it("does not run host IPC in the web runtime even if hostApi exists", async () => {
    vi.stubGlobal("window", {
      __WEB__: true,
      hostApi: {
        runOp: vi.fn().mockResolvedValue({ ok: true, value: { code: 0 } }),
      },
    });
    await performHostOp(payload({ request_id: "host-web" }), CID, "cloud");
    expect(window.hostApi?.runOp).not.toHaveBeenCalled();
    expect(resolveInteraction).toHaveBeenCalledWith(
      CID,
      "host-web",
      expect.objectContaining({
        kind: "client_tool",
        ok: false,
      }),
      "cloud",
    );
  });
});

describe("dispatch ignores cloud conversation-SSE host_op_required", () => {
  beforeEach(() => {
    resetClientToolFulfillmentForTests();
    resolveInteraction.mockClear();
    logEvent.mockClear();
    useConversationStore.setState({ currentConversationId: CID, byId: {} });
    useConversationStore.getState().switchConversation(CID);
    vi.stubGlobal("window", {
      hostApi: {
        runOp: vi.fn().mockResolvedValue({ ok: true, value: { code: 0 } }),
      },
    });
  });

  it("stopping: does not settle or run host IPC", () => {
    useConversationStore.getState().setTurnPhase("stopping", CID);
    const p = payload({ request_id: "h-stop" });
    dispatchSSEEvent(
      { type: "host_op_required", payload: p, timestamp: "t0" } as never,
      { conversationId: CID, source: "server" },
    );
    expect(logEvent).toHaveBeenCalledWith(
      "warn",
      "client_tool.ignored_on_conversation_sse",
      expect.objectContaining({
        event_type: "host_op_required",
        reason: "fulfill_channel_owns_client_tool",
      }),
    );
    expect(window.hostApi?.runOp).not.toHaveBeenCalled();
    expect(resolveInteraction).not.toHaveBeenCalled();
  });

  it("terminal: does not settle or run host IPC", () => {
    useConversationStore.getState().setTurnPhase("completed", CID);
    const p = payload({ request_id: "h-term-dispatch" });
    dispatchSSEEvent(
      { type: "host_op_required", payload: p, timestamp: "t0" } as never,
      { conversationId: CID, source: "server" },
    );
    expect(resolveInteraction).not.toHaveBeenCalled();
    expect(window.hostApi?.runOp).not.toHaveBeenCalled();
  });
});
