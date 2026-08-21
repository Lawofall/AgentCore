/**
 * SidecarManager deliverMessage / cancelQueuedTurn RPC (本机 live 插话禁止走云 POST).
 * @vitest-environment node
 */
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-deliver-${Math.random().toString(36).slice(2)}`,
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

vi.mock("../outbox/projection", () => ({
  occupyLocalTurnBegin: vi.fn(async () => true),
  abortLocalTurnPlaceholder: vi.fn(async () => undefined),
}));

import { rmSync } from "node:fs";
import { SidecarManager } from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

function rpcTransport(replies: Record<string, unknown>) {
  let lineCb: ((line: string) => void) | null = null;
  const sent: Array<{ method: string; params: Record<string, unknown> }> = [];
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as {
        id?: number;
        method?: string;
        params?: Record<string, unknown>;
      };
      if (typeof msg.id !== "number" || !msg.method) return;
      sent.push({ method: msg.method, params: msg.params ?? {} });
      const result = replies[msg.method];
      Promise.resolve().then(() => {
        if (
          result &&
          typeof result === "object" &&
          "error" in result &&
          result.error &&
          typeof result.error === "object"
        ) {
          const err = result.error as { code?: number; message?: string };
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              error: {
                code: err.code ?? -32000,
                message: err.message ?? "error",
              },
            }),
          );
          return;
        }
        if (result instanceof Error) {
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              error: { code: -32000, message: result.message },
            }),
          );
          return;
        }
        lineCb?.(
          JSON.stringify({
            jsonrpc: "2.0",
            id: msg.id,
            result: result ?? { ok: true },
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
  return { transport, sent };
}

const FIFO_IDS = {
  userMessageId: "11111111-1111-4111-8111-111111111111",
  messageId: "22222222-2222-4222-8222-222222222222",
  traceId: "a".repeat(32),
};

describe("SidecarManager deliverMessage / cancelQueuedTurn", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("deliverMessage 把 content/delivery 转成 RPC，回执 queued", async () => {
    const t = rpcTransport({
      initialize: { ok: true },
      deliverMessage: {
        status: "queued",
        queueId: "q1",
        position: 1,
        queueDepth: 1,
      },
    });
    const manager = new SidecarManager(() => t.transport);
    await manager.probe("r1", "", "/tmp/ws");
    const ack = await manager.deliverMessage({
      rootId: "r1",
      conversationId: "c1",
      content: "插一句",
      delivery: "queue",
      ...FIFO_IDS,
    });
    expect(ack).toEqual({
      status: "queued",
      queueId: "q1",
      position: 1,
      queueDepth: 1,
    });
    expect(t.sent.find((s) => s.method === "deliverMessage")?.params).toEqual({
      conversationId: "c1",
      content: "插一句",
      delivery: "queue",
      ...FIFO_IDS,
    });
  });

  it("deliverMessage 热挂起 RPC −32006 → blocked ack（同云 409）", async () => {
    const t = rpcTransport({
      initialize: { ok: true },
      deliverMessage: {
        error: { code: -32006, message: "pending interactions awaiting" },
      },
    });
    const manager = new SidecarManager(() => t.transport);
    await manager.probe("r1", "", "/tmp/ws");
    await expect(
      manager.deliverMessage({
        rootId: "r1",
        conversationId: "c1",
        content: "x",
        delivery: "steer",
        ...FIFO_IDS,
      }),
    ).resolves.toEqual({
      status: "blocked",
      code: "pending_interactions_awaiting",
    });
  });

  it("deliverMessage 无 sidecar 进程须抛错（不得当发送成功）", async () => {
    const manager = new SidecarManager(() => {
      throw new Error("must not spawn");
    });
    await expect(
      manager.deliverMessage({
        rootId: "missing",
        conversationId: "c1",
        content: "x",
        delivery: "steer",
        ...FIFO_IDS,
      }),
    ).rejects.toThrow(/未运行/);
  });

  it("cancelQueuedTurn RPC -32007 → status not_found", async () => {
    const t = rpcTransport({
      initialize: { ok: true },
      cancelQueuedTurn: {
        error: { code: -32007, message: "queued turn not found" },
      },
    });
    const manager = new SidecarManager(() => t.transport);
    await manager.probe("r1", "", "/tmp/ws");
    await expect(
      manager.cancelQueuedTurn({
        rootId: "r1",
        conversationId: "c1",
        queueId: "gone",
      }),
    ).resolves.toEqual({ status: "not_found" });
  });

  it("cancelQueuedTurn 无进程 → not_found（队随进程消失）", async () => {
    const manager = new SidecarManager(() => {
      throw new Error("must not spawn");
    });
    await expect(
      manager.cancelQueuedTurn({
        rootId: "r1",
        conversationId: "c1",
        queueId: "q1",
      }),
    ).resolves.toEqual({ status: "not_found" });
  });
});
