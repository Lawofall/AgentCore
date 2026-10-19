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
  persistAuthCookies: vi.fn(async () => {}),
}));

import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { SIDECAR_CHANNELS } from "@shared/sidecar-contract";
import {
  outboxDir,
  resetOccupiedConversationIdsProviderForTests,
} from "../outbox-writeback";
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
  session: {
    permissionAxes: { boundary: "folder" as const },
    delegateMode: false,
    folderId: null as string | null,
    localRootId: null as string | null,
    localSubpath: null as string | null,
  },
};

function capturingTransport(opts?: { startTurnError?: string }) {
  const sent: Array<{ method?: string; params?: Record<string, unknown> }> = [];
  let lineCb: ((line: string) => void) | null = null;
  let closeCb: ((err?: Error) => void) | null = null;
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
    onClose: (cb) => {
      closeCb = cb;
    },
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

  return { transport, sent, notify, die: (err?: Error) => closeCb?.(err) };
}

function writeOpenOutbox(
  userMessageId: string,
  overrides: Record<string, unknown> = {},
) {
  const dir = outboxDir();
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, `${userMessageId}.json`),
    JSON.stringify({
      schema_version: 1,
      user_message_id: userMessageId,
      conversation_id: START_REQ.conversationId,
      message_id: START_REQ.messageId,
      trace_id: START_REQ.traceId,
      user_message: START_REQ.userMessage,
      content: "partial reply",
      phase: "open",
      finish_reason: null,
      runs: null,
      journal: undefined,
      reasoning_content: null,
      stream_segments: undefined,
      ops: ["begin_turn"],
      ...overrides,
    }),
    "utf-8",
  );
}

describe("SidecarManager local-turn occupy", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));
  afterEach(() => {
    resetLocalTurnProjectionForTests();
    resetOccupiedConversationIdsProviderForTests();
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
    expect(manager.occupancy({ conversationId: "c-occupy" }).occupied).toBe(
      true,
    );
    expect(String(h.bearerPostJson.mock.calls[0]?.[0])).toBe(
      "/v1/conversations/c-occupy/local-turns/begin",
    );
    resolveBegin({ ok: true, status: 200, body: {} });
    await turnP;

    const start = t.sent.find((m) => m.method === "startTurn");
    expect(start?.params?.messageId).toBe(START_REQ.messageId);
    expect(start?.params?.traceId).toBe(TRACE);
    expect(start?.params?.userMessageId).toBe(START_REQ.userMessageId);
    expect(h.bearerPostJson.mock.calls[0]?.[1]).toEqual(
      expect.not.objectContaining({ regenerate: true }),
    );
  });

  it("idle occupy POSTs attachments on first send", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {},
    });
    const shot = {
      name: "shot.png",
      path: "shot.png",
      workspace_path: "attachments/shot.png",
      binary: true,
    };
    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        ...START_REQ,
        attachments: [shot],
      },
      "/tmp/ws-occupy",
    );
    expect(h.bearerPostJson.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        user_message: "hello",
        attachments: [shot],
      }),
    );
    expect(h.bearerPostJson.mock.calls[0]?.[1]).toEqual(
      expect.not.objectContaining({ regenerate: true }),
    );
  });

  it("regenerate occupy POSTs truncate flag and empty materials", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {},
    });
    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      {
        ...START_REQ,
        regenerate: true,
        replaceMaterials: true,
        attachments: [],
        agentMentions: [],
      },
      "/tmp/ws-occupy",
    );
    expect(h.bearerPostJson.mock.calls[0]?.[1]).toEqual({
      user_message: "hello",
      user_message_id: START_REQ.userMessageId,
      message_id: START_REQ.messageId,
      trace_id: TRACE,
      regenerate: true,
      agent_mentions: [],
      attachments: [],
    });
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

  it("RPC failure POSTs abort when no salvageable OPEN", async () => {
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

  it("RPC failure with salvageable OPEN does not POST abort", async () => {
    writeOpenOutbox(START_REQ.userMessageId);
    const t = capturingTransport({ startTurnError: "engine boom" });
    const manager = new SidecarManager(() => t.transport);
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {},
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: START_REQ.userMessageId,
        assistant_message_id: null,
        title: null,
        noop: true,
      },
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
    expect(paths).not.toContain("/v1/conversations/c-occupy/local-turns/abort");
    expect(paths).toContain("/v1/conversations/c-occupy/local-turns");
    const body = h.bearerPostJson.mock.calls.find((c) =>
      String(c[0]).endsWith("/local-turns"),
    )?.[1] as { finish_reason?: string; content?: string };
    expect(body.finish_reason).toBe("error");
    expect(body.content).toBe("partial reply");
  });

  it("busy slot aborts placeholder even if OPEN looks salvageable", async () => {
    writeOpenOutbox(START_REQ.userMessageId);
    const t = capturingTransport({
      startTurnError: "turn already running: live-turn",
    });
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
    ).rejects.toMatchObject({
      message: "turn already running: live-turn",
    });
    const paths = h.bearerPostJson.mock.calls.map((c) => String(c[0]));
    expect(paths[0]).toBe("/v1/conversations/c-occupy/local-turns/begin");
    expect(paths).toContain("/v1/conversations/c-occupy/local-turns/abort");
    expect(paths.filter((p) => p.endsWith("/local-turns"))).toEqual([]);
  });

  it("sidecar close salvages salvageable OPEN without abort", async () => {
    writeOpenOutbox(START_REQ.userMessageId);
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: START_REQ.userMessageId,
        assistant_message_id: null,
        title: null,
        noop: true,
      },
    });
    await manager.probe(START_REQ.rootId, "", "/tmp/ws-occupy");
    (
      manager as unknown as {
        turns: Map<
          string,
          { conversationId: string; rootId: string; subpath: string }
        >;
      }
    ).turns.set("t-dead", {
      conversationId: START_REQ.conversationId,
      rootId: START_REQ.rootId,
      subpath: "",
    });
    t.die(new Error("sidecar exited"));
    await vi.waitFor(
      () =>
        expect(h.bearerPostJson).toHaveBeenCalledWith(
          "/v1/conversations/c-occupy/local-turns",
          expect.objectContaining({
            user_message_id: START_REQ.userMessageId,
            finish_reason: "interrupted",
            content: "partial reply",
          }),
        ),
      { timeout: 5_000 },
    );
    const paths = h.bearerPostJson.mock.calls.map((c) => String(c[0]));
    expect(paths).not.toContain("/v1/conversations/c-occupy/local-turns/abort");
  });

  it("sidecar close does not salvage OPEN of another conversation still writing", async () => {
    writeOpenOutbox(START_REQ.userMessageId);
    writeOpenOutbox("55555555-5555-4555-8555-555555555555", {
      conversation_id: "c-live",
      message_id: "m-live",
      user_message: "other still writing",
      content: "live partial",
    });
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    (
      manager as unknown as {
        turns: Map<
          string,
          { conversationId: string; rootId: string; subpath: string }
        >;
      }
    ).turns.set("t-live", {
      conversationId: "c-live",
      rootId: "r-other",
      subpath: "",
    });
    (
      manager as unknown as {
        turns: Map<
          string,
          { conversationId: string; rootId: string; subpath: string }
        >;
      }
    ).turns.set("t-dead", {
      conversationId: START_REQ.conversationId,
      rootId: START_REQ.rootId,
      subpath: "",
    });
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        user_message_id: START_REQ.userMessageId,
        assistant_message_id: null,
        title: null,
        noop: true,
      },
    });
    await manager.probe(START_REQ.rootId, "", "/tmp/ws-occupy");
    t.die(new Error("sidecar exited"));
    await vi.waitFor(
      () =>
        expect(h.bearerPostJson).toHaveBeenCalledWith(
          `/v1/conversations/${START_REQ.conversationId}/local-turns`,
          expect.objectContaining({
            user_message_id: START_REQ.userMessageId,
          }),
        ),
      { timeout: 5_000 },
    );
    const convPaths = h.bearerPostJson.mock.calls.map((c) => String(c[0]));
    expect(convPaths).not.toContain("/v1/conversations/c-live/local-turns");
  });

  it("queue/needStart 交给渲染进程开跑，主进程不自己 startTurn", async () => {
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
    expect(wc.send).toHaveBeenCalledWith(
      SIDECAR_CHANNELS.queueNeedStart,
      expect.objectContaining({
        conversationId: fifo.conversationId,
        rootId: START_REQ.rootId,
        subpath: "",
        queueId: "q-fifo",
        userMessageId: fifo.userMessageId,
        messageId: fifo.messageId,
        traceId: fifo.traceId,
        userMessage: "下一句",
      }),
    );
    expect(t.sent.some((m) => m.method === "startTurn")).toBe(false);
    expect(h.bearerPostJson).not.toHaveBeenCalled();
  });
});
