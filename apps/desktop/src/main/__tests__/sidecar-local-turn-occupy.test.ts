/**
 * Local-turn occupy: begin before startTurn RPC; abort on RPC failure.
 * @vitest-environment node
 */
import { afterAll, afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-occupy-test-${Math.random().toString(36).slice(2)}`,
    bearerPostJson: vi.fn(),
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

vi.mock("../auth-client", () => ({
  bearerPostJson: h.bearerPostJson,
  refreshAccessToken: vi.fn(async () => "renewed" as const),
}));

import { rmSync } from "node:fs";
import { resetLocalTurnProjectionForTests } from "../outbox/projection";
import { SidecarManager } from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

const TRACE = "a".repeat(32);
const START_REQ = {
  conversationId: "c-occupy",
  rootId: "r1",
  turnId: "t_occupy",
  traceId: TRACE,
  userMessageId: "11111111-1111-4111-8111-111111111111",
  messageId: "22222222-2222-4222-8222-222222222222",
  userMessage: "hello",
};

function capturingTransport(opts?: { startTurnError?: string }) {
  const sent: Array<{ method?: string; params?: Record<string, unknown> }> = [];
  let lineCb: ((line: string) => void) | null = null;
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as {
        id?: number;
        method?: string;
        params?: Record<string, unknown>;
      };
      sent.push({ method: msg.method, params: msg.params });
      if (typeof msg.id !== "number" || !msg.method) return;
      Promise.resolve().then(() => {
        if (msg.method === "startTurn" && opts?.startTurnError) {
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              error: { code: -32603, message: opts.startTurnError },
            }),
          );
          return;
        }
        lineCb?.(
          JSON.stringify({
            jsonrpc: "2.0",
            id: msg.id,
            result:
              msg.method === "initialize"
                ? { ok: true }
                : {
                    turnId: START_REQ.turnId,
                    messageId: START_REQ.messageId,
                    content: "",
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
                  },
          }),
        );
      });
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: () => {},
    close: vi.fn(),
  };
  function notify(method: string, params: Record<string, unknown>) {
    lineCb?.(
      JSON.stringify({
        jsonrpc: "2.0",
        method,
        params,
      }),
    );
  }

  return { transport, sent, notify };
}

describe("SidecarManager local-turn occupy", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));
  afterEach(() => {
    resetLocalTurnProjectionForTests();
    h.bearerPostJson.mockReset();
  });

  it("POSTs begin before startTurn RPC and forwards messageId + 32-hex traceId", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    let resolveBegin!: (value: {
      ok: boolean;
      status: number;
      body: unknown;
    }) => void;
    h.bearerPostJson.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBegin = resolve;
        }),
    );

    const turnP = manager.startTurn(wc as never, START_REQ, "/tmp/ws-occupy");
    await vi.waitFor(() => expect(h.bearerPostJson).toHaveBeenCalled());
    expect(t.sent.some((m) => m.method === "startTurn")).toBe(false);
    expect(String(h.bearerPostJson.mock.calls[0]?.[0])).toBe(
      "/v1/conversations/c-occupy/local-turns/begin",
    );
    resolveBegin({ ok: true, status: 200, body: {} });
    await turnP;

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params?.messageId).toBe(START_REQ.messageId);
    expect(start?.params?.traceId).toBe(TRACE);
    expect(start?.params?.userMessageId).toBe(START_REQ.userMessageId);
  });

  it("begin failure does not send startTurn RPC", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    h.bearerPostJson.mockResolvedValue({
      ok: false,
      status: 503,
      body: {},
    });
    await expect(
      manager.startTurn(
        { isDestroyed: () => false, send: vi.fn() } as never,
        START_REQ,
        "/tmp/ws-occupy",
      ),
    ).rejects.toThrow(/占位失败/);
    expect(t.sent.some((m) => m.method === "startTurn")).toBe(false);
  });

  it("RPC failure POSTs abort", async () => {
    const t = capturingTransport({ startTurnError: "engine boom" });
    const manager = new SidecarManager(() => t.transport);
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {},
    });
    await expect(
      manager.startTurn(
        { isDestroyed: () => false, send: vi.fn() } as never,
        START_REQ,
        "/tmp/ws-occupy",
      ),
    ).rejects.toMatchObject({ message: "engine boom" });
    const paths = h.bearerPostJson.mock.calls.map((c) => String(c[0]));
    expect(paths[0]).toBe("/v1/conversations/c-occupy/local-turns/begin");
    expect(paths).toContain("/v1/conversations/c-occupy/local-turns/abort");
    expect(h.bearerPostJson.mock.calls[1]?.[1]).toEqual({
      user_message_id: START_REQ.userMessageId,
      message_id: START_REQ.messageId,
    });
  });

  it("queue/needStart occupies then startTurn with queueId", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {},
    });
    await manager.startTurn(wc as never, START_REQ, "/tmp/ws-occupy");
    h.bearerPostJson.mockClear();
    t.sent.length = 0;

    const fifo = {
      conversationId: START_REQ.conversationId,
      queueId: "q-fifo",
      userMessageId: "33333333-3333-4333-8333-333333333333",
      messageId: "44444444-4444-4444-8444-444444444444",
      traceId: "b".repeat(32),
      userMessage: "下一句",
    };
    t.notify("queue/needStart", fifo);
    await vi.waitFor(() =>
      expect(t.sent.some((m) => m.method === "startTurn")).toBe(true),
    );
    expect(String(h.bearerPostJson.mock.calls[0]?.[0])).toBe(
      "/v1/conversations/c-occupy/local-turns/begin",
    );
    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params).toEqual(
      expect.objectContaining({
        conversationId: fifo.conversationId,
        queueId: "q-fifo",
        userMessageId: fifo.userMessageId,
        messageId: fifo.messageId,
        traceId: fifo.traceId,
        userMessage: "下一句",
      }),
    );
    const beginIdx = t.sent.findIndex((m) => m.method === "startTurn");
    expect(h.bearerPostJson).toHaveBeenCalled();
    expect(beginIdx).toBeGreaterThanOrEqual(0);
  });
});
