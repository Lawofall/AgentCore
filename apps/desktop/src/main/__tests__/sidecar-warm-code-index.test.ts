/**
 * SidecarManager.warmCodeIndex explicitly kicks warm RPC; ensure does not.
 * @vitest-environment node
 */
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-warm-test-${Math.random().toString(36).slice(2)}`,
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
      if (typeof msg.id === "number" && msg.method) {
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
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: () => {},
    close: vi.fn(),
  };
  return { transport, sent };
}

describe("SidecarManager warmCodeIndex", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("warmCodeIndex IPC sends warm after initialize", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmCodeIndex("r1", "", "/tmp/ws-warm");

    expect(t.sent.map((m) => m.method)).toEqual([
      "initialize",
      "warmCodeIndex",
    ]);
  });

  it("ensure cache hit does not kick warmCodeIndex", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.probe("r1", "", "/tmp/ws-warm2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmCodeIndex").length).toBe(0);

    await manager.probe("r1", "", "/tmp/ws-warm2");
    // Cache hit: still no warm; single initialize.
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmCodeIndex").length).toBe(0);
  });
});
