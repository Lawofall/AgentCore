/**
 * SidecarManager.warmMcpDiscover lists via mcp-service then kicks RPC with userId;
 * ensure does not auto-kick; startTurn awaits in-flight MCP warm.
 * @vitest-environment node
 */
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-warm-mcp-test-${Math.random().toString(36).slice(2)}`,
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

vi.mock("../mcp-service", () => ({
  listMcpToolsValue: h.listMcpToolsValue,
}));

import { rmSync } from "node:fs";
import { SidecarManager } from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

function capturingTransport(opts?: {
  /** Delay warmMcpDiscover RPC reply (ms) to assert startTurn awaits. */
  warmDelayMs?: number;
}) {
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
      if (typeof msg.id === "number" && msg.method) {
        const delay =
          msg.method === "warmMcpDiscover" && opts?.warmDelayMs
            ? opts.warmDelayMs
            : 0;
        const reply = () => {
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              result: { ok: true, ttlSeconds: 300 },
            }),
          );
        };
        if (delay > 0) {
          setTimeout(reply, delay);
        } else {
          Promise.resolve().then(reply);
        }
      }
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: () => {},
    close: vi.fn(),
  };
  return { transport, sent };
}

const ACCOUNT_USER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

describe("SidecarManager warmMcpDiscover", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("warmMcpDiscover lists then sends warm RPC with userId after initialize", async () => {
    h.listMcpToolsValue.mockReset();
    h.listMcpToolsValue.mockResolvedValue({
      servers: [
        {
          id: "echo",
          name: "Echo",
          status: "ready",
          tools: [{ name: "ping" }],
        },
      ],
    });
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmMcpDiscover("r1", "", "/tmp/ws-warm-mcp", {
      userId: ACCOUNT_USER,
    });

    expect(h.listMcpToolsValue).toHaveBeenCalledTimes(1);
    expect(t.sent.map((m) => m.method)).toEqual([
      "initialize",
      "warmMcpDiscover",
    ]);
    const init = t.sent.find((m) => m.method === "initialize");
    expect(init?.params?.userId).toBe(ACCOUNT_USER);
    const warm = t.sent.find((m) => m.method === "warmMcpDiscover");
    expect(warm?.params).toEqual({
      servers: [
        {
          id: "echo",
          name: "Echo",
          status: "ready",
          tools: [{ name: "ping" }],
        },
      ],
      userId: ACCOUNT_USER,
    });
  });

  it("ensure cache hit does not kick warmMcpDiscover", async () => {
    h.listMcpToolsValue.mockReset();
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.probe("r1", "", "/tmp/ws-warm-mcp2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmMcpDiscover").length).toBe(0);
    expect(h.listMcpToolsValue).not.toHaveBeenCalled();

    await manager.probe("r1", "", "/tmp/ws-warm-mcp2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmMcpDiscover").length).toBe(0);
    expect(h.listMcpToolsValue).not.toHaveBeenCalled();
  });

  it("startTurn awaits in-flight MCP warm before startTurn RPC", async () => {
    h.listMcpToolsValue.mockReset();
    h.listMcpToolsValue.mockResolvedValue({ servers: [] });
    const t = capturingTransport({ warmDelayMs: 40 });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    const warmP = manager.warmMcpDiscover("r-await", "", "/tmp/ws-await", {
      userId: ACCOUNT_USER,
    });
    // Let warm RPC leave (initialize done, warm reply still delayed).
    await vi.waitFor(() => {
      expect(t.sent.some((m) => m.method === "warmMcpDiscover")).toBe(true);
    });
    expect(t.sent.some((m) => m.method === "startTurn")).toBe(false);

    const turnP = manager.startTurn(
      wc as never,
      {
        conversationId: "c-await",
        rootId: "r-await",
        turnId: "turn-await",
        traceId: "a".repeat(32),
        userMessageId: "u1",
        userMessage: "hi",
        userId: ACCOUNT_USER,
      },
      "/tmp/ws-await",
    );

    await Promise.all([warmP, turnP]);
    const methods = t.sent.map((m) => m.method);
    const warmIdx = methods.indexOf("warmMcpDiscover");
    const turnIdx = methods.indexOf("startTurn");
    expect(warmIdx).toBeGreaterThanOrEqual(0);
    expect(turnIdx).toBeGreaterThan(warmIdx);
  });
});
