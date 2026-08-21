/**
 * Detached execution 存活期按 TTL 周期续暖：startTurn RPC 已返回后团队仍跑，
 * 跨过 300s 后 sidecar 内部收口仍能命中 rules/memory 与 MCP 缓存。
 * @vitest-environment node
 */
import { afterAll, afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-warm-keepalive-${Math.random().toString(36).slice(2)}`,
    listMcpToolsValue: vi.fn(),
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

vi.mock("../mcp-service", () => ({
  listMcpToolsValue: h.listMcpToolsValue,
}));

import { rmSync } from "node:fs";
import { SidecarManager } from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

function capturingTransport() {
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
      const isWarm =
        msg.method === "warmAccountRulesMemory" ||
        msg.method === "warmMcpDiscover";
      Promise.resolve().then(() => {
        lineCb?.(
          JSON.stringify({
            jsonrpc: "2.0",
            id: msg.id,
            result: isWarm ? { ok: true, ttlSeconds: 300 } : { ok: true },
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
  return {
    transport,
    sent,
    notifyTurnEvent(
      turnId: string,
      type: string,
      payload: Record<string, unknown>,
      conversationId: string,
    ) {
      lineCb?.(
        JSON.stringify({
          jsonrpc: "2.0",
          method: "turn/event",
          params: {
            turnId,
            conversationId,
            event: {
              type,
              timestamp: new Date().toISOString(),
              payload,
            },
          },
        }),
      );
    },
  };
}

const accountAuth = {
  baseUrl: "https://api.example.com/v1/account",
  apiKey: "acct-tok",
};

const EXECUTION_ID = "exec-detached-keep";

describe("SidecarManager execution warm keepalive", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("re-warms after startTurn returned while detached execution crosses TTL", async () => {
    h.listMcpToolsValue.mockReset();
    h.listMcpToolsValue.mockResolvedValue({ servers: [] });
    vi.useFakeTimers({
      toFake: ["Date", "setTimeout", "clearTimeout"],
    });

    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    await manager.warmMcpDiscover("r-keep", "", "/tmp/ws-keep", {
      userId: "user-keep",
    });
    await manager.startTurn(
      wc as never,
      {
        conversationId: "c-keep",
        rootId: "r-keep",
        turnId: "turn-keep",
        traceId: "a".repeat(32),
        userMessageId: "u-keep",
        messageId: "m-asst",
        userMessage: "long task",
        folderId: "folder-keep",
        accountAuth,
        userId: "user-keep",
      },
      "/tmp/ws-keep",
    );

    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmMcpDiscover").length).toBe(1);

    // 事故现场：CEO 回合 RPC 已返回，团队 detached 继续跑。
    t.notifyTurnEvent(
      "turn-keep",
      "execution_detached",
      {
        execution_id: EXECUTION_ID,
        conversation_id: "c-keep",
        completed: 0,
        total: 2,
      },
      "c-keep",
    );

    // 300s TTL − 15s 续期余量：detached 跨过窗口后必须再暖，收口 cache_only 才命中。
    await vi.advanceTimersByTimeAsync(286_000);
    for (let i = 0; i < 40; i++) await Promise.resolve();
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(2);
    expect(t.sent.filter((m) => m.method === "warmMcpDiscover").length).toBe(2);

    t.notifyTurnEvent(
      "turn-keep",
      "execution_completed",
      {
        execution_id: EXECUTION_ID,
        conversation_id: "c-keep",
        completed: 2,
        total: 2,
        status: "completed",
      },
      "c-keep",
    );
    await vi.advanceTimersByTimeAsync(286_000);
    for (let i = 0; i < 40; i++) await Promise.resolve();
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(2);
    expect(t.sent.filter((m) => m.method === "warmMcpDiscover").length).toBe(2);

    manager.disposeAll();
  });
});
