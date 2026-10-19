/**
 * SidecarManager.warmLlmHttp sends RPC with inference after initialize;
 * ensure/probe do not auto-kick; startTurn does not await this warm.
 * @vitest-environment node
 */
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-warm-llm-http-test-${Math.random().toString(36).slice(2)}`,
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

const INFERENCE = {
  baseUrl: "http://proxy.test/v1/inference/v1",
  apiKey: "tok-warm",
  model: "flash",
};
const ACCOUNT_USER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

function capturingTransport(opts?: { warmDelayMs?: number }) {
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
          msg.method === "warmLlmHttp" && opts?.warmDelayMs
            ? opts.warmDelayMs
            : 0;
        const reply = () => {
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              result: { ok: true },
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

describe("SidecarManager warmLlmHttp", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("sends initialize + warmLlmHttp with inference", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmLlmHttp("r1", "conv/c1", "/tmp/ws-warm-llm", {
      inference: INFERENCE,
      userId: ACCOUNT_USER,
    });

    expect(t.sent.map((m) => m.method)).toEqual(["initialize", "warmLlmHttp"]);
    const init = t.sent.find((m) => m.method === "initialize");
    expect(init?.params?.userId).toBe(ACCOUNT_USER);
    expect(init?.params?.inference).toEqual(INFERENCE);
    const warm = t.sent.find((m) => m.method === "warmLlmHttp");
    expect(warm?.params).toEqual({
      inference: INFERENCE,
      userId: ACCOUNT_USER,
    });
  });

  it("ensure / probe cache hit does not kick warmLlmHttp", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.probe("r1", "", "/tmp/ws-warm-llm2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmLlmHttp").length).toBe(0);

    await manager.probe("r1", "", "/tmp/ws-warm-llm2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "warmLlmHttp").length).toBe(0);
  });

  it("startTurn does not await in-flight warmLlmHttp", async () => {
    const t = capturingTransport({ warmDelayMs: 80 });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    const warmP = manager.warmLlmHttp("r-await", "", "/tmp/ws-await-llm", {
      inference: INFERENCE,
      userId: ACCOUNT_USER,
    });
    await vi.waitFor(() => {
      expect(t.sent.some((m) => m.method === "warmLlmHttp")).toBe(true);
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
        messageId: "m-asst",
        userMessage: "hi",
        userId: ACCOUNT_USER,
        inference: INFERENCE,
        session: {
          permissionAxes: { boundary: "folder" as const },
          delegateMode: false,
          folderId: null,
          localRootId: null,
          localSubpath: null,
        },
      },
      "/tmp/ws-await-llm",
    );

    await turnP;
    expect(t.sent.some((m) => m.method === "startTurn")).toBe(true);
    await warmP;
  });
});
