import { useConversationStore } from "@/stores/conversation";
import type { SidecarUnsyncedTurnSummary } from "@shared/sidecar-contract";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/streamConversation", () => ({
  dispatchSSEEvent: vi.fn(),
  flushPendingContent: vi.fn(),
  flushPendingFrames: vi.fn(),
}));

vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(async () => ({
      live_running: false,
      paused: [],
      pending_interactions: [],
    })),
  },
}));

import { dispatchSSEEvent } from "@/services/streamConversation";
import { resetSidecarEventPumpForTests } from "../sidecarEventPump";
import {
  clearActiveSidecarTurn,
  getActiveSidecarTarget,
  setActiveSidecarTurn,
} from "../sidecarRouting";
import { projectUnsyncedTurns } from "../turns/projectUnsynced";
import {
  attachSidecarTurn,
  resetSidecarAttachInFlightForTests,
} from "../turns/sidecarAttach";
import { resetStreamOwnershipForTests } from "../turns/streamOwnership";

const CID = "conv-sidecar-recover";
const dispatchMock = vi.mocked(dispatchSSEEvent);

type EventPush = {
  conversationId: string;
  turnId: string;
  event: unknown;
};

let onEventCb: ((push: EventPush) => void) | null;
let onEventCalls: number;

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  clearActiveSidecarTurn(CID);
  resetSidecarEventPumpForTests();
  resetSidecarAttachInFlightForTests();
  resetStreamOwnershipForTests();
  dispatchMock.mockClear();
  onEventCb = null;
  onEventCalls = 0;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubSidecarApi(over: {
  attach?: ReturnType<typeof vi.fn>;
  recovery?: ReturnType<typeof vi.fn>;
  cancel?: ReturnType<typeof vi.fn>;
}): void {
  vi.stubGlobal("window", {
    __WEB__: false,
    sidecarApi: {
      attach: over.attach ?? vi.fn(async () => ({ attached: false })),
      recovery:
        over.recovery ??
        vi.fn(async () => ({
          liveRunning: false,
          unsynced: [],
          paused: [],
        })),
      cancel: over.cancel ?? vi.fn(),
      onEvent: vi.fn((cb: (push: EventPush) => void) => {
        onEventCalls += 1;
        onEventCb = cb;
        return () => {
          if (onEventCb === cb) onEventCb = null;
        };
      }),
    },
  });
}

function unsyncedReady(
  over: Partial<SidecarUnsyncedTurnSummary> = {},
): SidecarUnsyncedTurnSummary {
  return {
    user_message_id: "u-ready",
    user_message: "prior q",
    message_id: "a-ready",
    trace_id: "t".repeat(32),
    phase: "ready",
    updated_at: 100,
    content: "prior answer",
    reasoning_content: null,
    citations: [],
    runs: { events: [], finish_reason: "stop" },
    finish_reason: "stop",
    input_tokens: 1,
    output_tokens: 2,
    reasoning_tokens: 0,
    cache_hit_tokens: 0,
    cache_miss_tokens: 0,
    ...over,
  };
}

function attachLiveResponse(over: Record<string, unknown> = {}) {
  return {
    attached: true as const,
    turnId: "turn-live",
    rootId: "root-1",
    subpath: "",
    kind: "start" as const,
    userMessageId: "u-live",
    userMessage: "live q",
    traceId: "f".repeat(32),
    events: [
      {
        type: "message_start",
        timestamp: "t0",
        payload: { message_id: "a-live" },
      },
      {
        type: "content_delta",
        timestamp: "t1",
        payload: { delta: "hello" },
      },
      {
        type: "message_end",
        timestamp: "t2",
        payload: { finish_reason: "stop" },
      },
    ],
    ...over,
  };
}

describe("projectUnsyncedTurns (D5)", () => {
  it("projects ready rows with synced_pending and skips duplicate ids", () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage(
      {
        id: "u-ready",
        role: "user",
        content: "already from cloud",
        createdAt: "2026-01-01T00:00:00Z",
        executionId: null,
        isStreaming: false,
      },
      CID,
    );

    projectUnsyncedTurns(CID, [
      unsyncedReady(),
      unsyncedReady({
        user_message_id: "u-new",
        message_id: "a-new",
        user_message: "new q",
        content: "new a",
        updated_at: 200,
      }),
    ]);

    const msgs = useConversationStore.getState().byId[CID].messages;
    expect(msgs.filter((m) => m.id === "u-ready")).toHaveLength(1);
    expect(msgs.find((m) => m.id === "u-new")?.content).toBe("new q");
    expect(msgs.find((m) => m.id === "a-new")?.content).toBe("new a");
    expect(msgs.find((m) => m.id === "u-new")?.syncStatus).toBe(
      "synced_pending",
    );
  });

  it("marks open ghost as interrupted incomplete", () => {
    useConversationStore.getState().switchConversation(CID);
    projectUnsyncedTurns(CID, [
      unsyncedReady({
        user_message_id: "u-open",
        message_id: "a-open",
        phase: "open",
        content: "partial",
        finish_reason: null,
        runs: null,
      }),
    ]);
    const assistant = useConversationStore
      .getState()
      .byId[CID].messages.find((m) => m.id === "a-open");
    expect(assistant?.status).toBe("incomplete");
    expect(assistant?.isStreaming).toBe(false);
    expect(assistant?.finishReason).toBe("interrupted");
  });

  it("empty cancelled ready keeps cancelled finish (B5 空泡脸)", () => {
    useConversationStore.getState().switchConversation(CID);
    projectUnsyncedTurns(CID, [
      unsyncedReady({
        user_message_id: "u-cancel",
        message_id: "a-cancel",
        phase: "ready",
        content: "",
        finish_reason: "cancelled",
        runs: { events: [], finish_reason: "cancelled", process: null },
      }),
    ]);
    const assistant = useConversationStore
      .getState()
      .byId[CID].messages.find((m) => m.id === "a-cancel");
    expect(assistant?.content).toBe("");
    expect(assistant?.isStreaming).toBe(false);
    expect(assistant?.status).toBe("incomplete");
    expect(assistant?.finishReason).toBe("cancelled");
  });

  it("empty dead seals as interrupted (B5 orphan)", () => {
    useConversationStore.getState().switchConversation(CID);
    projectUnsyncedTurns(CID, [
      unsyncedReady({
        user_message_id: "u-dead",
        message_id: "a-dead",
        phase: "dead",
        content: "",
        finish_reason: null,
        runs: null,
      }),
    ]);
    const assistant = useConversationStore
      .getState()
      .byId[CID].messages.find((m) => m.id === "a-dead");
    expect(assistant?.finishReason).toBe("interrupted");
    expect(assistant?.status).toBe("incomplete");
  });
});

describe("attachSidecarTurn (D4)", () => {
  it("synthesizes user row, setActive before fold, replays terminal", async () => {
    useConversationStore.getState().switchConversation(CID);

    const attachMock = vi.fn(async () => attachLiveResponse());
    stubSidecarApi({ attach: attachMock });

    const ok = await attachSidecarTurn(CID);
    expect(ok).toBe(true);
    expect(attachMock).toHaveBeenCalledWith({ conversationId: CID });
    expect(onEventCalls).toBe(1);

    const msgs = useConversationStore.getState().byId[CID].messages;
    expect(msgs.some((m) => m.id === "u-live" && m.content === "live q")).toBe(
      true,
    );
    expect(dispatchMock.mock.calls.map((c) => c[0].type)).toEqual([
      "message_start",
      "content_delta",
      "message_end",
    ]);
    expect(getActiveSidecarTarget(CID)).toBeNull();
    expect(msgs.find((m) => m.id === "u-live")?.syncStatus).toBe(
      "synced_pending",
    );
  });

  it("overlay isGenerating still attaches (cold hydrate chrome is not a pump)", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.setGenerating(true, CID);
    const attachMock = vi.fn(async () => attachLiveResponse());
    stubSidecarApi({ attach: attachMock });
    expect(await attachSidecarTurn(CID)).toBe(true);
    expect(attachMock).toHaveBeenCalledWith({ conversationId: CID });
    expect(onEventCalls).toBe(1);
  });

  it("explicit AbortSignal detaches viewer without cancelling the engine (C1)", async () => {
    useConversationStore.getState().switchConversation(CID);

    let resolveAttach!: (v: ReturnType<typeof attachLiveResponse>) => void;
    const attachGate = new Promise<ReturnType<typeof attachLiveResponse>>(
      (resolve) => {
        resolveAttach = resolve;
      },
    );
    const cancelMock = vi.fn();
    const attachMock = vi.fn(() => attachGate);
    stubSidecarApi({
      attach: attachMock,
      cancel: cancelMock,
    });

    const ac = new AbortController();
    const p = attachSidecarTurn(CID, { signal: ac.signal });
    await vi.waitFor(() => expect(attachMock).toHaveBeenCalled());

    // Snapshot without terminal — attach waits on live tail.
    resolveAttach(
      attachLiveResponse({
        events: [
          {
            type: "message_start",
            timestamp: "t0",
            payload: { message_id: "a-live" },
          },
        ],
      }),
    );
    await vi.waitFor(() =>
      expect(getActiveSidecarTarget(CID)?.turnId).toBe("turn-live"),
    );

    // 显式卸观察：abort 观察泵，引擎必须继续（切会话不走此路径）。
    ac.abort();
    await expect(p).resolves.toBe(true);
    expect(cancelMock).not.toHaveBeenCalled();
    expect(getActiveSidecarTarget(CID)).toBeNull();
    // generating cleared so reopen hydrate can attach again.
    expect(useConversationStore.getState().byId[CID]?.isGenerating).toBe(false);
  });

  it("without external abort, attach keeps isGenerating until natural terminal", async () => {
    useConversationStore.getState().switchConversation(CID);

    // Mirror fold: message_end clears generating (teardown only clears on viewer abort).
    dispatchMock.mockImplementation((event) => {
      if (event.type === "message_end" || event.type === "error") {
        useConversationStore.getState().setGenerating(false, CID);
      }
    });

    let resolveAttach!: (v: ReturnType<typeof attachLiveResponse>) => void;
    const attachGate = new Promise<ReturnType<typeof attachLiveResponse>>(
      (resolve) => {
        resolveAttach = resolve;
      },
    );
    const cancelMock = vi.fn();
    const attachMock = vi.fn(() => attachGate);
    stubSidecarApi({
      attach: attachMock,
      cancel: cancelMock,
    });

    const p = attachSidecarTurn(CID);
    await vi.waitFor(() => expect(attachMock).toHaveBeenCalled());

    resolveAttach(
      attachLiveResponse({
        events: [
          {
            type: "message_start",
            timestamp: "t0",
            payload: { message_id: "a-live" },
          },
        ],
      }),
    );
    await vi.waitFor(() =>
      expect(getActiveSidecarTarget(CID)?.turnId).toBe("turn-live"),
    );
    expect(useConversationStore.getState().byId[CID]?.isGenerating).toBe(true);

    // Live terminal ends the turn — no external AbortSignal.
    onEventCb?.({
      conversationId: CID,
      turnId: "turn-live",
      event: {
        type: "message_end",
        timestamp: "t-end",
        payload: { finish_reason: "stop" },
      },
    });
    await expect(p).resolves.toBe(true);
    expect(cancelMock).not.toHaveBeenCalled();
    expect(getActiveSidecarTarget(CID)).toBeNull();
    expect(useConversationStore.getState().byId[CID]?.isGenerating).toBe(false);
  });

  it("attached:false does not leave generating hung (fact-driven re-query)", async () => {
    useConversationStore.getState().switchConversation(CID);

    stubSidecarApi({
      attach: vi.fn(async () => ({ attached: false })),
      recovery: vi.fn(async () => ({
        liveRunning: false,
        unsynced: [],
        paused: [],
      })),
    });

    const ok = await attachSidecarTurn(CID);
    expect(ok).toBe(false);
    expect(
      useConversationStore.getState().byId[CID]?.isGenerating ?? false,
    ).toBe(false);
  });

  it("concurrent dual attach / hydrate twice → one IPC + one content_delta dispatch", async () => {
    useConversationStore.getState().switchConversation(CID);

    let releaseAttach!: (v: ReturnType<typeof attachLiveResponse>) => void;
    const attachGate = new Promise<ReturnType<typeof attachLiveResponse>>(
      (resolve) => {
        releaseAttach = resolve;
      },
    );
    const attachMock = vi.fn(() => attachGate);
    stubSidecarApi({ attach: attachMock });

    const p1 = attachSidecarTurn(CID);
    const p2 = attachSidecarTurn(CID);
    expect(attachMock).toHaveBeenCalledTimes(1);

    releaseAttach(
      attachLiveResponse({
        events: [
          {
            type: "message_start",
            timestamp: "t0",
            payload: { message_id: "a-live" },
          },
          {
            type: "content_delta",
            timestamp: "t1",
            payload: { delta: "once" },
          },
          {
            type: "message_end",
            timestamp: "t2",
            payload: { finish_reason: "stop" },
          },
        ],
      }),
    );

    await expect(Promise.all([p1, p2])).resolves.toEqual([true, true]);
    expect(onEventCalls).toBe(1);
    const deltas = dispatchMock.mock.calls.filter(
      (c) => c[0].type === "content_delta",
    );
    expect(deltas).toHaveLength(1);
    expect(deltas[0][0].payload).toEqual({ delta: "once" });
  });

  it("live content_delta after attach is dispatched once (single pump)", async () => {
    useConversationStore.getState().switchConversation(CID);

    let resolveDone!: () => void;
    const turnDone = new Promise<void>((r) => {
      resolveDone = r;
    });

    const attachMock = vi.fn(async () => {
      // Snapshot without terminal — live tail must finish the turn.
      queueMicrotask(() => {
        onEventCb?.({
          conversationId: CID,
          turnId: "turn-live",
          event: {
            type: "content_delta",
            timestamp: "t-live",
            payload: { delta: "tok" },
          },
        });
        onEventCb?.({
          conversationId: CID,
          turnId: "turn-live",
          event: {
            type: "message_end",
            timestamp: "t-end",
            payload: { finish_reason: "stop" },
          },
        });
        resolveDone();
      });
      return attachLiveResponse({
        events: [
          {
            type: "message_start",
            timestamp: "t0",
            payload: { message_id: "a-live" },
          },
        ],
      });
    });
    stubSidecarApi({ attach: attachMock });

    const ok = await attachSidecarTurn(CID);
    await turnDone;
    expect(ok).toBe(true);
    expect(onEventCalls).toBe(1);
    const deltas = dispatchMock.mock.calls.filter(
      (c) => c[0].type === "content_delta",
    );
    expect(deltas).toHaveLength(1);
    expect(deltas[0][0].payload).toEqual({ delta: "tok" });
  });

  it("overlay generating still claims active sidecar target for stop/steer", async () => {
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.setGenerating(true, CID);
    // Stale chrome from cold GET running — not a live startTurn pump.
    setActiveSidecarTurn(CID, "root-stale", "", "turn-stale");

    let resolveAttach!: (v: ReturnType<typeof attachLiveResponse>) => void;
    const attachGate = new Promise<ReturnType<typeof attachLiveResponse>>(
      (resolve) => {
        resolveAttach = resolve;
      },
    );
    const attachMock = vi.fn(() => attachGate);
    stubSidecarApi({ attach: attachMock });

    const p = attachSidecarTurn(CID);
    await vi.waitFor(() => expect(attachMock).toHaveBeenCalled());
    resolveAttach(
      attachLiveResponse({
        events: [
          {
            type: "message_start",
            timestamp: "t0",
            payload: { message_id: "a-live" },
          },
        ],
      }),
    );
    await vi.waitFor(() =>
      expect(getActiveSidecarTarget(CID)?.turnId).toBe("turn-live"),
    );

    onEventCb?.({
      conversationId: CID,
      turnId: "turn-live",
      event: {
        type: "message_end",
        timestamp: "t-end",
        payload: { finish_reason: "stop" },
      },
    });
    await expect(p).resolves.toBe(true);
    expect(getActiveSidecarTarget(CID)).toBeNull();
  });
});

describe("clearActiveSidecarTurn turnId match", () => {
  it("does not clear when turnId mismatches", () => {
    setActiveSidecarTurn(CID, "r1", "", "turn-A");
    clearActiveSidecarTurn(CID, "turn-B");
    expect(getActiveSidecarTarget(CID)?.rootId).toBe("r1");
    clearActiveSidecarTurn(CID, "turn-A");
    expect(getActiveSidecarTarget(CID)).toBeNull();
  });
});
