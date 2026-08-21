/**
 * SidecarManager forwards account userId on initialize + startTurn (not hardcoded "local").
 * @vitest-environment node
 */
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-userid-test-${Math.random().toString(36).slice(2)}`,
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
              result:
                msg.method === "initialize"
                  ? { ok: true }
                  : {
                      turnId: "t1",
                      messageId: "m1",
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

describe("SidecarManager userId passthrough", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("initialize + startTurn use account userId when provided", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    await manager.startTurn(
      wc as never,
      {
        conversationId: "c1",
        rootId: "r1",
        turnId: "turn-1",
        traceId: "a".repeat(32),
        userId: "acct-uuid-99",
        userMessageId: "u1",
        messageId: "m-asst",
        userMessage: "hello",
      },
      "/tmp/ws",
    );

    const init = t.sent.find((m) => m.method === "initialize");
    const start = t.sent.find((m) => m.method === "startTurn");
    expect(init?.params?.userId).toBe("acct-uuid-99");
    expect(start?.params?.userId).toBe("acct-uuid-99");
  });

  it("initialize falls back to local when userId absent", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    await manager.startTurn(
      wc as never,
      {
        conversationId: "c2",
        rootId: "r2",
        turnId: "turn-2",
        traceId: "b".repeat(32),
        userMessageId: "u2",
        messageId: "m-asst",
        userMessage: "hello",
      },
      "/tmp/ws",
    );

    const init = t.sent.find((m) => m.method === "initialize");
    const start = t.sent.find((m) => m.method === "startTurn");
    expect(init?.params?.userId).toBe("local");
    expect(start?.params?.userId).toBeUndefined();
  });

  it("startTurn RPC always includes folderId (project id or null bare chat)", async () => {
    const withFolder = capturingTransport();
    const managerA = new SidecarManager(() => withFolder.transport);
    await managerA.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c3",
        rootId: "r3",
        turnId: "turn-3",
        traceId: "c".repeat(32),
        userMessageId: "u3",
        messageId: "m-asst",
        userMessage: "hello",
        folderId: "fold-abc",
      },
      "/tmp/ws",
    );
    const startA = withFolder.sent.find((m) => m.method === "startTurn");
    expect(startA?.params?.folderId).toBe("fold-abc");

    const bare = capturingTransport();
    const managerB = new SidecarManager(() => bare.transport);
    await managerB.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c4",
        rootId: "r4",
        turnId: "turn-4",
        traceId: "d".repeat(32),
        userMessageId: "u4",
        messageId: "m-asst",
        userMessage: "hello",
        folderId: null,
      },
      "/tmp/ws",
    );
    const startB = bare.sent.find((m) => m.method === "startTurn");
    expect(startB?.params).toHaveProperty("folderId");
    expect(startB?.params?.folderId).toBeNull();
  });

  it("startTurn RPC always includes localRootId/localSubpath (binding or null)", async () => {
    const withBinding = capturingTransport();
    const managerA = new SidecarManager(() => withBinding.transport);
    await managerA.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c5",
        rootId: "r5",
        subpath: "apps/web",
        turnId: "turn-5",
        traceId: "e".repeat(32),
        userMessageId: "u5",
        messageId: "m-asst",
        userMessage: "hello",
        folderId: "fold-local",
        localRootId: "r5",
        localSubpath: "apps/web",
      },
      "/tmp/ws",
    );
    const startA = withBinding.sent.find((m) => m.method === "startTurn");
    expect(startA?.params?.localRootId).toBe("r5");
    expect(startA?.params?.localSubpath).toBe("apps/web");
    // Routing keys stay off the stdio params.
    expect(startA?.params).not.toHaveProperty("rootId");
    expect(startA?.params).not.toHaveProperty("subpath");

    const bare = capturingTransport();
    const managerB = new SidecarManager(() => bare.transport);
    await managerB.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c6",
        rootId: "r6",
        turnId: "turn-6",
        traceId: "f".repeat(32),
        userMessageId: "u6",
        messageId: "m-asst",
        userMessage: "hello",
        folderId: null,
      },
      "/tmp/ws",
    );
    const startB = bare.sent.find((m) => m.method === "startTurn");
    expect(startB?.params).toHaveProperty("localRootId");
    expect(startB?.params?.localRootId).toBeNull();
    expect(startB?.params).toHaveProperty("localSubpath");
    expect(startB?.params?.localSubpath).toBeNull();
  });

  it("startTurn RPC includes foldersAuth when provided", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-folders",
        rootId: "r-folders",
        turnId: "turn-folders",
        traceId: "1".repeat(32),
        userMessageId: "u-folders",
        messageId: "m-asst",
        userMessage: "hello",
        foldersAuth: {
          baseUrl: "https://api.test.example",
          apiKey: "folders-jwt",
        },
      },
      "/tmp/ws",
    );

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params?.foldersAuth).toEqual({
      baseUrl: "https://api.test.example",
      apiKey: "folders-jwt",
    });
  });

  it("startTurn RPC omits foldersAuth when mint absent", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-no-folders",
        rootId: "r-no-folders",
        turnId: "turn-no-folders",
        traceId: "2".repeat(32),
        userMessageId: "u-no-folders",
        messageId: "m-asst",
        userMessage: "hello",
      },
      "/tmp/ws",
    );

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params).not.toHaveProperty("foldersAuth");
  });

  it("startTurn RPC includes accountAuth when provided", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-account",
        rootId: "r-account",
        turnId: "turn-account",
        traceId: "a".repeat(32),
        userMessageId: "u-account",
        messageId: "m-asst",
        userMessage: "hello",
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "account-jwt",
        },
      },
      "/tmp/ws",
    );

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params?.accountAuth).toEqual({
      baseUrl: "https://api.test.example/v1/account",
      apiKey: "account-jwt",
    });
  });

  it("startTurn RPC omits history when renderer did not confirm a window", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-no-history",
        rootId: "r-no-history",
        turnId: "turn-no-history",
        traceId: "c".repeat(32),
        userMessageId: "u-no-history",
        messageId: "m-asst",
        userMessage: "hello",
      },
      "/tmp/ws",
    );

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params).not.toHaveProperty("history");
  });

  it("startTurn RPC forwards confirmed history including an empty window", async () => {
    const emptyT = capturingTransport();
    const managerA = new SidecarManager(() => emptyT.transport);
    await managerA.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-empty-history",
        rootId: "r-empty-history",
        turnId: "turn-empty-history",
        traceId: "d".repeat(32),
        userMessageId: "u-empty-history",
        messageId: "m-asst",
        userMessage: "hello",
        history: [],
      },
      "/tmp/ws",
    );
    const emptyStart = emptyT.sent.find((m) => m.method === "startTurn");
    expect(emptyStart?.params?.history).toEqual([]);

    const rowsT = capturingTransport();
    const managerB = new SidecarManager(() => rowsT.transport);
    await managerB.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-rows-history",
        rootId: "r-rows-history",
        turnId: "turn-rows-history",
        traceId: "e".repeat(32),
        userMessageId: "u-rows-history",
        messageId: "m-asst",
        userMessage: "hello",
        history: [{ role: "user", content: "先前问" }],
      },
      "/tmp/ws",
    );
    const rowsStart = rowsT.sent.find((m) => m.method === "startTurn");
    expect(rowsStart?.params?.history).toEqual([
      { role: "user", content: "先前问" },
    ]);
  });

  it("startTurn RPC omits accountAuth when mint absent", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-no-account",
        rootId: "r-no-account",
        turnId: "turn-no-account",
        traceId: "b".repeat(32),
        userMessageId: "u-no-account",
        messageId: "m-asst",
        userMessage: "hello",
      },
      "/tmp/ws",
    );

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params).not.toHaveProperty("accountAuth");
  });

  it("resume RPC always includes folderId (project id or null bare chat)", async () => {
    const withFolder = capturingTransport();
    const managerA = new SidecarManager(() => withFolder.transport);
    await managerA.resume(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-resume",
        rootId: "r-resume",
        messageId: "m-asst",
        traceId: "3".repeat(32),
        decision: "continue",
        note: "",
        folderId: "fold-resume",
      },
      "/tmp/ws",
      undefined,
    );
    const resumeA = withFolder.sent.find((m) => m.method === "resume");
    expect(resumeA?.params?.folderId).toBe("fold-resume");

    const bare = capturingTransport();
    const managerB = new SidecarManager(() => bare.transport);
    await managerB.resume(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        conversationId: "c-resume-bare",
        rootId: "r-resume-bare",
        messageId: "m-asst-2",
        traceId: "4".repeat(32),
        decision: "continue",
        note: "",
        folderId: null,
      },
      "/tmp/ws",
      undefined,
    );
    const resumeB = bare.sent.find((m) => m.method === "resume");
    expect(resumeB?.params).toHaveProperty("folderId");
    expect(resumeB?.params?.folderId).toBeNull();
  });
});
