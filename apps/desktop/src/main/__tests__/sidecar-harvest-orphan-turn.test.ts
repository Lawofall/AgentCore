/**
 * SidecarManager forwards harvest `turn/event` for an unknown turnId when
 * conversationId still maps to the last window (after startTurn finally).
 * @vitest-environment node
 */
import { afterAll, afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-harvest-orphan-${Math.random().toString(36).slice(2)}`,
    windows: [] as Array<{
      isDestroyed: () => boolean;
      webContents: {
        isDestroyed: () => boolean;
        send: (...args: unknown[]) => void;
      };
    }>,
  };
});

vi.mock("electron", () => ({
  app: { on: vi.fn(), getAppPath: () => "", getPath: () => h.dir },
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getAllWindows: () => h.windows },
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

vi.mock("../outbox/projection", () => ({
  occupyLocalTurnBegin: vi.fn(async () => true),
  abortLocalTurnPlaceholder: vi.fn(async () => undefined),
}));

import { rmSync } from "node:fs";
import { SidecarManager } from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

const TURN_RESULT = {
  messageId: "m1",
  content: "hi",
  reasoningContent: null,
  finishReason: "stop",
  model: "x",
  rounds: 1,
  usage: {
    inputTokens: 0,
    outputTokens: 0,
    reasoningTokens: 0,
    cacheHitTokens: 0,
    cacheMissTokens: 0,
  },
  citations: [],
  runs: null,
  error: null,
};

function harvestTransport() {
  let lineCb: ((line: string) => void) | null = null;
  let closeCb: ((err?: Error) => void) | null = null;
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as {
        id?: number;
        method?: string;
      };
      if (typeof msg.id === "number" && msg.method) {
        Promise.resolve().then(() => {
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              result:
                msg.method === "initialize"
                  ? { ok: true }
                  : msg.method === "listQueuedTurns"
                    ? { items: [] }
                    : { turnId: "t1", ...TURN_RESULT },
            }),
          );
        });
      }
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: (cb) => {
      closeCb = cb;
    },
    close: vi.fn(() => {
      closeCb?.(new Error("sidecar 进程已退出"));
    }),
  };

  function notify(
    turnId: string,
    event: Record<string, unknown>,
    conversationId?: string,
    extra?: Record<string, unknown>,
  ) {
    lineCb?.(
      JSON.stringify({
        jsonrpc: "2.0",
        method: "turn/event",
        params: {
          turnId,
          ...(conversationId ? { conversationId } : {}),
          ...extra,
          event,
        },
      }),
    );
  }

  function notifyFulfill(event: Record<string, unknown>) {
    lineCb?.(
      JSON.stringify({
        jsonrpc: "2.0",
        method: "fulfill/frame",
        params: { event },
      }),
    );
  }

  return { transport, notify, notifyFulfill };
}

function mockWc(destroyed = false) {
  const state = { destroyed };
  return {
    isDestroyed: () => state.destroyed,
    send: vi.fn(),
    destroy() {
      state.destroyed = true;
    },
  };
}

function asWindow(wc: ReturnType<typeof mockWc>) {
  return {
    isDestroyed: () => wc.isDestroyed(),
    webContents: wc,
  };
}

function eventTypes(wc: ReturnType<typeof mockWc>): string[] {
  return (wc.send as ReturnType<typeof vi.fn>).mock.calls
    .map((c) => (c[1] as { event?: { type?: string } }).event?.type)
    .filter((t): t is string => Boolean(t));
}

function sentEvents(wc: ReturnType<typeof mockWc>) {
  return (wc.send as ReturnType<typeof vi.fn>).mock.calls.map(
    (c) =>
      c[1] as {
        turnId?: string;
        event?: { type?: string; payload?: Record<string, unknown> };
      },
  );
}

/** Hang startTurn/resume so tests can inject events or RPC errors first. */
function hangingTransport() {
  let lineCb: ((line: string) => void) | null = null;
  const pending = new Map<number, string>();
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as { id?: number; method?: string };
      if (typeof msg.id === "number" && msg.method) {
        pending.set(msg.id, msg.method);
        if (msg.method === "initialize") {
          Promise.resolve().then(() => {
            lineCb?.(
              JSON.stringify({
                jsonrpc: "2.0",
                id: msg.id,
                result: { ok: true },
              }),
            );
          });
        } else if (msg.method === "listQueuedTurns") {
          Promise.resolve().then(() => {
            lineCb?.(
              JSON.stringify({
                jsonrpc: "2.0",
                id: msg.id,
                result: { items: [] },
              }),
            );
          });
        }
      }
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: () => {},
    close: vi.fn(),
  };

  function notify(turnId: string, event: Record<string, unknown>) {
    lineCb?.(
      JSON.stringify({
        jsonrpc: "2.0",
        method: "turn/event",
        params: { turnId, event },
      }),
    );
  }

  function settleTurn(result: Record<string, unknown> = { ...TURN_RESULT }) {
    for (const [id, method] of pending) {
      if (method === "startTurn" || method === "resume") {
        lineCb?.(JSON.stringify({ jsonrpc: "2.0", id, result }));
        pending.delete(id);
      }
    }
  }

  function rejectTurn(error: { code: number; message: string }) {
    for (const [id, method] of pending) {
      if (method === "startTurn" || method === "resume") {
        lineCb?.(JSON.stringify({ jsonrpc: "2.0", id, error }));
        pending.delete(id);
      }
    }
  }

  return { transport, notify, settleTurn, rejectTurn };
}

async function waitLive(
  manager: SidecarManager,
  conversationId: string,
): Promise<void> {
  for (let i = 0; i < 100; i++) {
    const r = await manager.recovery({ conversationId });
    if (r.liveRunning) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error(`live turn for ${conversationId} never registered`);
}

describe("SidecarManager harvest orphan turn/event", () => {
  afterEach(() => {
    h.windows.length = 0;
  });
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("forwards unknown turnId + conversationId after startTurn finally", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-harvest",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "a".repeat(32),
        userMessageId: "u1",
        messageId: "m-asst",
        userMessage: "hello",
      },
      "/tmp/ws",
    );
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "收口" },
      },
      "c-harvest",
    );

    const sent = (wc.send as ReturnType<typeof vi.fn>).mock.calls.map(
      (c) =>
        c[1] as {
          conversationId?: string;
          turnId?: string;
          event?: { type?: string; payload?: { delta?: string } };
        },
    );
    const harvest = sent.find((p) => p.turnId === "turn-harvest");
    expect(harvest).toBeDefined();
    expect(harvest?.conversationId).toBe("c-harvest");
    expect(harvest?.event?.type).toBe("content_delta");
    expect(harvest?.event?.payload?.delta).toBe("收口");
  });

  it("does not forward unknown turnId without conversationId", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-no-cid",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "b".repeat(32),
        userMessageId: "u2",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.notify("turn-orphan", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "lost" },
    });

    expect(wc.send).not.toHaveBeenCalled();
  });

  it("falls back to getAllWindows when remembered wc is destroyed", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const dead = mockWc();
    await manager.startTurn(
      dead as never,
      {
        conversationId: "c-dead-wc",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "c".repeat(32),
        userMessageId: "u3",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    dead.destroy();
    (dead.send as ReturnType<typeof vi.fn>).mockClear();

    const fallback = mockWc();
    h.windows.push(asWindow(fallback));

    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "收口" },
      },
      "c-dead-wc",
    );

    expect(dead.send).not.toHaveBeenCalled();
    const sent = (fallback.send as ReturnType<typeof vi.fn>).mock.calls.map(
      (c) =>
        c[1] as {
          turnId?: string;
          event?: { type?: string; payload?: { delta?: string } };
        },
    );
    const harvest = sent.find((p) => p.turnId === "turn-harvest");
    expect(harvest?.event?.type).toBe("content_delta");
    expect(harvest?.event?.payload?.delta).toBe("收口");
  });

  it("does not treat a destroyed remembered wc as routable", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const dead = mockWc();
    await manager.startTurn(
      dead as never,
      {
        conversationId: "c-dead-only",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "d".repeat(32),
        userMessageId: "u4",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    dead.destroy();
    (dead.send as ReturnType<typeof vi.fn>).mockClear();

    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "lost" },
      },
      "c-dead-only",
    );

    expect(dead.send).not.toHaveBeenCalled();
  });

  it("routes fulfill/frame to an ephemeral harvest turn", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-fulfill",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "e".repeat(32),
        userMessageId: "u5",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "收口" },
      },
      "c-fulfill",
    );
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.notifyFulfill({
      type: "workspace_op_required",
      timestamp: "t1",
      payload: { conversation_id: "c-fulfill", request_id: "op-1" },
    });

    const fulfill = (wc.send as ReturnType<typeof vi.fn>).mock.calls.find(
      (c) => c[0] === "sidecar:fulfill",
    );
    expect(fulfill).toBeDefined();
    expect(fulfill?.[1]).toMatchObject({
      conversationId: "c-fulfill",
      frame: {
        type: "workspace_op_required",
        payload: { conversation_id: "c-fulfill", request_id: "op-1" },
      },
    });
  });

  it("attach does not treat harvest ephemeral as a live user turn", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-attach",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "f".repeat(32),
        userMessageId: "u6",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "收口" },
      },
      "c-attach",
    );
    expect(
      manager.attach(mockWc() as never, { conversationId: "c-attach" })
        .attached,
    ).toBe(false);
  });

  it("attach treats FIFO origin=queue as a live user turn", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-fifo",
        rootId: "r1",
        turnId: "turn-host",
        traceId: "a".repeat(32),
        userMessageId: "u-host",
        messageId: "m-host",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    t.notify(
      "turn-fifo",
      {
        type: "turn_queue_started",
        timestamp: "t0",
        payload: { queue_id: "q1", content: "下一句" },
      },
      "c-fifo",
      {
        origin: "queue",
        userMessageId: "u-fifo",
        messageId: "m-fifo",
        traceId: "b".repeat(32),
      },
    );
    const attached = manager.attach(mockWc() as never, {
      conversationId: "c-fifo",
    });
    expect(attached.attached).toBe(true);
    expect(attached.turnId).toBe("turn-fifo");
  });

  it("synthesizes D4 error for ephemeral harvest when sidecar exits (no hasAttached)", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-d4-close",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "g".repeat(32),
        userMessageId: "u7",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "半截" },
      },
      "c-d4-close",
    );
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.transport.close();

    const harvestErr = sentEvents(wc).find((p) => p.turnId === "turn-harvest");
    expect(harvestErr?.event?.type).toBe("error");
    expect(harvestErr?.event?.payload).toMatchObject({
      code: "sidecar_turn_ended",
    });
    expect(eventTypes(wc)).not.toContain("message_end");
  });

  it("synthesizes D4 message_end when a new user turn drops ephemeral harvest", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-d4-drop",
        rootId: "r1",
        turnId: "turn-user",
        traceId: "h".repeat(32),
        userMessageId: "u8",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    t.notify(
      "turn-harvest",
      {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "半截" },
      },
      "c-d4-drop",
    );
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-d4-drop",
        rootId: "r1",
        turnId: "turn-user-2",
        traceId: "i".repeat(32),
        userMessageId: "u9",
        messageId: "m-asst",
        userMessage: "next",
      },
      "/tmp/ws",
    );

    const harvestEnd = sentEvents(wc).find((p) => p.turnId === "turn-harvest");
    expect(harvestEnd?.event?.type).toBe("message_end");
  });

  it("synthesizes end_turn message_end for live turn with no attach and no terminal", async () => {
    const t = harvestTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-live-no-terminal",
        rootId: "r1",
        turnId: "turn-live",
        traceId: "j".repeat(32),
        userMessageId: "u10",
        messageId: "m-asst",
        userMessage: "stop me",
      },
      "/tmp/ws",
    );

    const ends = sentEvents(wc).filter((p) => p.event?.type === "message_end");
    expect(ends).toHaveLength(1);
    expect(ends[0]?.turnId).toBe("turn-live");
    expect(ends[0]?.event?.payload).toEqual({ finish_reason: "end_turn" });
    expect(eventTypes(wc)).not.toContain("error");
  });

  it("does not synthesize a second terminal when buffer already has one", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    const turnP = manager.startTurn(
      wc as never,
      {
        conversationId: "c-already-terminal",
        rootId: "r1",
        turnId: "turn-dup",
        traceId: "k".repeat(32),
        userMessageId: "u11",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c-already-terminal");
    t.notify("turn-dup", {
      type: "message_end",
      timestamp: "t1",
      payload: { finish_reason: "stop" },
    });
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.settleTurn({ turnId: "turn-dup", ...TURN_RESULT });
    await turnP;

    const ends = sentEvents(wc).filter((p) => p.event?.type === "message_end");
    expect(ends).toHaveLength(0);
    expect(eventTypes(wc)).not.toContain("error");
  });

  it("synthesizes cancelled message_end on TURN_CANCELLED, not error", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    const turnP = manager.startTurn(
      wc as never,
      {
        conversationId: "c-rpc-cancel",
        rootId: "r1",
        turnId: "turn-cancel",
        traceId: "l".repeat(32),
        userMessageId: "u12",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c-rpc-cancel");
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.rejectTurn({ code: -32001, message: "turn cancelled" });
    await expect(turnP).rejects.toMatchObject({
      code: -32001,
      message: "turn cancelled",
    });

    const ends = sentEvents(wc).filter((p) => p.event?.type === "message_end");
    expect(ends).toHaveLength(1);
    expect(ends[0]?.event?.payload).toEqual({ finish_reason: "cancelled" });
    expect(eventTypes(wc)).not.toContain("error");
  });

  it("synthesizes cancelled message_end on resume cancel, not error", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    const turnP = manager.resume(
      wc as never,
      {
        rootId: "r1",
        conversationId: "c-resume-cancel",
        messageId: "msg-resume-cancel",
        traceId: "m".repeat(32),
        userMessageId: "u13",
        decision: "continue",
        note: "",
      },
      "/tmp/ws",
      undefined,
    );
    await waitLive(manager, "c-resume-cancel");
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.rejectTurn({ code: -32001, message: "turn cancelled" });
    await expect(turnP).rejects.toMatchObject({ message: "turn cancelled" });

    const ends = sentEvents(wc).filter((p) => p.event?.type === "message_end");
    expect(ends).toHaveLength(1);
    expect(ends[0]?.turnId).toBe("msg-resume-cancel");
    expect(ends[0]?.event?.payload).toEqual({ finish_reason: "cancelled" });
    expect(eventTypes(wc)).not.toContain("error");
  });

  it("synthesizes error on live engine failure, not cancelled message_end", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    const turnP = manager.startTurn(
      wc as never,
      {
        conversationId: "c-engine-fail",
        rootId: "r1",
        turnId: "turn-fail",
        traceId: "n".repeat(32),
        userMessageId: "u14",
        messageId: "m-asst",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c-engine-fail");
    (wc.send as ReturnType<typeof vi.fn>).mockClear();

    t.rejectTurn({ code: -32603, message: "engine boom" });
    await expect(turnP).rejects.toMatchObject({ message: "engine boom" });

    expect(eventTypes(wc)).toEqual(["error"]);
    expect(sentEvents(wc)[0]?.event?.payload).toMatchObject({
      code: "sidecar_turn_ended",
      message: "engine boom",
    });
  });
});
