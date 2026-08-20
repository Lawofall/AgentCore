import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-recovery-test-${Math.random().toString(36).slice(2)}`,
  };
});

vi.mock("electron", () => ({
  app: { on: vi.fn(), getAppPath: () => "", getPath: () => h.dir },
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getAllWindows: () => [] },
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

import {
  SidecarManager,
  isDestroyedWebContentsError,
} from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

/** Fake stdio transport: complete initialize, hang startTurn/resume, inject notifications. */
function hangingTransport() {
  let lineCb: ((line: string) => void) | null = null;
  const pending = new Map<number, string>();
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as {
        id?: number;
        method?: string;
      };
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

  function settleTurn(result: Record<string, unknown> = { turnId: "t1" }) {
    for (const [id, method] of pending) {
      if (method === "startTurn" || method === "resume") {
        lineCb?.(JSON.stringify({ jsonrpc: "2.0", id, result }));
        pending.delete(id);
      }
    }
  }

  return { transport, notify, settleTurn };
}

function mockWc(destroyed = false) {
  return {
    isDestroyed: () => destroyed,
    send: vi.fn(),
  };
}

const outboxDir = join(h.dir, "sidecar", "outbox");

function writeOutbox(record: Record<string, unknown>): void {
  mkdirSync(outboxDir, { recursive: true });
  const id = String(record.user_message_id);
  writeFileSync(join(outboxDir, `${id}.json`), JSON.stringify(record), "utf-8");
}

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

/** Wait until startTurn/resume has registered the live turn (past initialize). */
async function waitLive(
  manager: SidecarManager,
  conversationId: string,
): Promise<void> {
  for (let i = 0; i < 100; i++) {
    const r = await manager.recovery({ conversationId });
    if (r.liveRunning) return;
    await new Promise((r) => setTimeout(r, 0));
  }
  throw new Error(`live turn for ${conversationId} never registered`);
}

describe("SidecarManager recovery / attach (D7)", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("buffers events when wc is destroyed (no longer drops)", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const dead = mockWc(true);
    const turnP = manager.startTurn(
      dead as never,
      {
        conversationId: "c1",
        rootId: "r1",
        turnId: "turn-1",
        traceId: "a".repeat(32),
        userMessageId: "u1",
        userMessage: "hello",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c1");

    t.notify("turn-1", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "hi" },
    });
    t.notify("turn-1", {
      type: "message_end",
      timestamp: "t1",
      payload: {},
    });

    const attached = manager.attach(mockWc() as never, {
      conversationId: "c1",
    });
    expect(attached.attached).toBe(true);
    expect(attached.events?.map((e) => e.type)).toEqual([
      "content_delta",
      "message_end",
    ]);
    expect((attached.events?.[0].payload as { delta: string }).delta).toBe(
      "hi",
    );

    t.settleTurn({ turnId: "turn-1", ...TURN_RESULT });
    await turnP;
  });

  it("attach rebind→snapshot is zero-await atomic (inject mid-snapshot)", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc1 = mockWc();
    const turnP = manager.startTurn(
      wc1 as never,
      {
        conversationId: "c2",
        rootId: "r1",
        turnId: "turn-2",
        traceId: "b".repeat(32),
        userMessageId: "u2",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c2");

    t.notify("turn-2", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "a" },
    });

    const wc2 = mockWc();
    const live = (
      manager as unknown as {
        turns: Map<
          string,
          {
            buffer: {
              snapshot: () => unknown[];
              record: (e: unknown) => void;
            };
          }
        >;
      }
    ).turns.get("turn-2");
    expect(live).toBeDefined();
    if (!live) throw new Error("expected live turn buffer");
    const orig = live.buffer.snapshot.bind(live.buffer);
    let injected = false;
    live.buffer.snapshot = () => {
      if (!injected) {
        injected = true;
        t.notify("turn-2", {
          type: "content_delta",
          timestamp: "t1",
          payload: { delta: "b" },
        });
      }
      return orig();
    };

    const res = manager.attach(wc2 as never, { conversationId: "c2" });
    expect(res.attached).toBe(true);
    const deltas = (res.events ?? [])
      .filter((e) => e.type === "content_delta")
      .map((e) => (e.payload as { delta: string }).delta)
      .join("");
    const forwarded = (wc2.send as ReturnType<typeof vi.fn>).mock.calls
      .map(
        (c) =>
          c[1] as {
            event?: { type: string; payload?: { delta?: string } };
          },
      )
      .filter((p) => p.event?.type === "content_delta")
      .map((p) => p.event?.payload?.delta ?? "")
      .join("");
    expect(deltas).toBe("ab");
    expect(forwarded).toBe("");

    t.settleTurn({ turnId: "turn-2", ...TURN_RESULT, content: "ab" });
    await turnP;
  });

  it("attach is idempotent across repeat attach / mid-replay refresh", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const turnP = manager.startTurn(
      mockWc() as never,
      {
        conversationId: "c3",
        rootId: "r1",
        turnId: "turn-3",
        traceId: "c".repeat(32),
        userMessageId: "u3",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c3");
    t.notify("turn-3", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "x" },
    });

    const a1 = manager.attach(mockWc() as never, { conversationId: "c3" });
    t.notify("turn-3", {
      type: "content_delta",
      timestamp: "t1",
      payload: { delta: "y" },
    });
    const a2 = manager.attach(mockWc() as never, { conversationId: "c3" });
    expect(a1.attached && a2.attached).toBe(true);
    expect(a2.events?.length).toBeGreaterThanOrEqual(a1.events?.length ?? 0);

    t.settleTurn({ turnId: "turn-3", ...TURN_RESULT, content: "xy" });
    await turnP;
  });

  it("attached:false when turn already settled (race)", async () => {
    const manager = new SidecarManager(() => {
      throw new Error("must not spawn");
    });
    const res = manager.attach(mockWc() as never, { conversationId: "gone" });
    expect(res).toEqual({ attached: false });
  });

  it("synthesizes terminal before turns.delete when attached and no message_end", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    const turnP = manager.startTurn(
      mockWc(true) as never,
      {
        conversationId: "c4",
        rootId: "r1",
        turnId: "turn-4",
        traceId: "d".repeat(32),
        userMessageId: "u4",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c4");
    t.notify("turn-4", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "partial" },
    });

    const attached = manager.attach(wc as never, { conversationId: "c4" });
    expect(attached.attached).toBe(true);
    (wc.send as ReturnType<typeof vi.fn>).mockClear();
    t.settleTurn({
      turnId: "turn-4",
      ...TURN_RESULT,
      content: "partial",
    });
    await turnP;

    const types = (wc.send as ReturnType<typeof vi.fn>).mock.calls.map(
      (c) => (c[1] as { event?: { type: string } }).event?.type,
    );
    expect(types).toContain("message_end");
    const end = (wc.send as ReturnType<typeof vi.fn>).mock.calls
      .map((c) => c[1] as { event?: { type?: string; payload?: unknown } })
      .map((p) => p.event)
      .find((e) => e?.type === "message_end");
    expect(end?.payload).toEqual({ finish_reason: "end_turn" });
    expect(
      manager.attach(mockWc() as never, { conversationId: "c4" }).attached,
    ).toBe(false);
  });

  it("resume-kind turns are covered by the recovery surface (G4)", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const turnP = manager.resume(
      mockWc() as never,
      {
        rootId: "r1",
        conversationId: "c5",
        messageId: "msg-resume",
        traceId: "e".repeat(32),
        userMessageId: "u5",
        decision: "continue",
        note: "",
      },
      "/tmp/ws",
      undefined,
    );
    await waitLive(manager, "c5");

    t.notify("msg-resume", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "more" },
    });

    const recovery = await manager.recovery({ conversationId: "c5" });
    expect(recovery.liveRunning).toBe(true);
    expect(recovery.turnId).toBe("msg-resume");

    const attached = manager.attach(mockWc() as never, {
      conversationId: "c5",
    });
    expect(attached.attached).toBe(true);
    expect(attached.kind).toBe("resume");
    expect(attached.messageId).toBe("msg-resume");
    expect(attached.events?.[0]).toMatchObject({
      type: "content_delta",
      payload: { delta: "more" },
    });

    t.settleTurn({
      turnId: "msg-resume",
      ...TURN_RESULT,
      messageId: "msg-resume",
      content: "more",
    });
    await turnP;
  });

  it("recovery lists unsynced outbox summaries (ready + open with segment fill)", async () => {
    writeOutbox({
      user_message_id: "ready-1",
      conversation_id: "c6",
      message_id: "a-ready",
      user_message: "done q",
      content: "done answer",
      phase: "ready",
      updated_at: 100,
      finish_reason: "stop",
      runs: { events: [], finish_reason: "stop" },
    });
    writeOutbox({
      user_message_id: "open-ghost",
      conversation_id: "c6",
      message_id: "a-open",
      user_message: "ghost q",
      content: "",
      phase: "open",
      updated_at: 50,
      stream_segments: {
        "captain:content": { text: "partial from seg", generation: 1 },
      },
    });

    const manager = new SidecarManager(() => {
      throw new Error("recovery must not spawn");
    });
    const recovery = await manager.recovery({ conversationId: "c6" });
    expect(recovery.liveRunning).toBe(false);
    expect(recovery.unsynced.map((u) => u.user_message_id)).toEqual([
      "open-ghost",
      "ready-1",
    ]);
    expect(recovery.unsynced[0].content).toBe("partial from seg");
    expect(recovery.unsynced[1].phase).toBe("ready");
    expect(recovery.paused).toEqual([]);
  });

  it("swallows wc.send destroy race on notify without dropping buffer (D2)", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    (wc.send as ReturnType<typeof vi.fn>).mockImplementation(() => {
      throw new TypeError("Object has been destroyed");
    });
    const turnP = manager.startTurn(
      wc as never,
      {
        conversationId: "c-d2-race",
        rootId: "r1",
        turnId: "turn-d2",
        traceId: "f".repeat(32),
        userMessageId: "u-d2",
        userMessage: "hello",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c-d2-race");

    expect(() =>
      t.notify("turn-d2", {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "race" },
      }),
    ).not.toThrow();

    const attached = manager.attach(mockWc() as never, {
      conversationId: "c-d2-race",
    });
    expect(attached.attached).toBe(true);
    expect(attached.events?.map((e) => e.type)).toEqual(["content_delta"]);
    expect((attached.events?.[0].payload as { delta: string }).delta).toBe(
      "race",
    );

    t.settleTurn({ turnId: "turn-d2", ...TURN_RESULT, content: "race" });
    await turnP;
  });

  it("swallows destroy race on synthetic terminal send (D2)", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    const turnP = manager.startTurn(
      mockWc(true) as never,
      {
        conversationId: "c-d2-synth",
        rootId: "r1",
        turnId: "turn-d2-synth",
        traceId: "1".repeat(32),
        userMessageId: "u-d2-synth",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c-d2-synth");
    t.notify("turn-d2-synth", {
      type: "content_delta",
      timestamp: "t0",
      payload: { delta: "partial" },
    });
    expect(
      manager.attach(wc as never, { conversationId: "c-d2-synth" }).attached,
    ).toBe(true);

    (wc.send as ReturnType<typeof vi.fn>).mockImplementation(() => {
      throw new TypeError("Object has been destroyed");
    });
    t.settleTurn({
      turnId: "turn-d2-synth",
      ...TURN_RESULT,
      content: "partial",
    });
    await expect(turnP).resolves.toBeTruthy();
  });

  it("rethrows non-destroy wc.send errors from onNotification (D2)", async () => {
    const t = hangingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = mockWc();
    (wc.send as ReturnType<typeof vi.fn>).mockImplementation(() => {
      throw new Error("EPIPE: broken pipe");
    });
    const turnP = manager.startTurn(
      wc as never,
      {
        conversationId: "c-d2-real",
        rootId: "r1",
        turnId: "turn-d2-real",
        traceId: "2".repeat(32),
        userMessageId: "u-d2-real",
        userMessage: "q",
      },
      "/tmp/ws",
    );
    await waitLive(manager, "c-d2-real");

    expect(() =>
      t.notify("turn-d2-real", {
        type: "content_delta",
        timestamp: "t0",
        payload: { delta: "x" },
      }),
    ).toThrow(/EPIPE/);

    (wc.send as ReturnType<typeof vi.fn>).mockImplementation(() => {});
    t.settleTurn({ turnId: "turn-d2-real", ...TURN_RESULT });
    await turnP;
  });
});

describe("isDestroyedWebContentsError (D2)", () => {
  it("matches Electron destroy / frame-disposed messages only", () => {
    expect(
      isDestroyedWebContentsError(new TypeError("Object has been destroyed")),
    ).toBe(true);
    expect(
      isDestroyedWebContentsError(
        new Error(
          "Render frame was disposed before WebFrameMain could be accessed",
        ),
      ),
    ).toBe(true);
    expect(
      isDestroyedWebContentsError(new Error("WebFrameMain was disposed")),
    ).toBe(true);
    expect(isDestroyedWebContentsError(new Error("EPIPE: broken pipe"))).toBe(
      false,
    );
    expect(isDestroyedWebContentsError(new Error("channel closed"))).toBe(
      false,
    );
  });
});
