/**
 * SidecarManager.warmAccountRulesMemory + first startTurn kick;
 * no-auth skip does not lock; late login re-warms; ensure/probe do not kick.
 *
 * 续期契约：服务端快照有 TTL，过期即空注入且不回落云端，所以暖不能只做一次——
 * 这里按服务端回的 `ttlSeconds` 推进假时钟，验证过期后回合前会自动续暖。
 * @vitest-environment node
 */
import { afterAll, afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/sidecar-warm-rules-test-${Math.random().toString(36).slice(2)}`,
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

function capturingTransport(opts?: {
  warmDelayMs?: number;
  /** 暖回复里的 `ttlSeconds`（快照剩余寿命，秒）；`null` = 服务端没给这个字段。 */
  warmTtlSeconds?: number | null;
  /** 暖 RPC 直接报错（sidecar 不可达 / 内部错）。 */
  warmFails?: boolean;
}) {
  const sent: Array<{ method?: string; params?: Record<string, unknown> }> = [];
  let lineCb: ((line: string) => void) | null = null;
  const warmTtl =
    opts?.warmTtlSeconds === undefined ? 300 : opts.warmTtlSeconds;
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as {
        id?: number;
        method?: string;
        params?: Record<string, unknown>;
      };
      sent.push({ method: msg.method, params: msg.params });
      if (typeof msg.id === "number" && msg.method) {
        const isWarm = msg.method === "warmAccountRulesMemory";
        const delay = isWarm && opts?.warmDelayMs ? opts.warmDelayMs : 0;
        const reply = () => {
          lineCb?.(
            JSON.stringify(
              isWarm && opts?.warmFails
                ? {
                    jsonrpc: "2.0",
                    id: msg.id,
                    error: { code: -32603, message: "warm boom" },
                  }
                : {
                    jsonrpc: "2.0",
                    id: msg.id,
                    result: isWarm
                      ? {
                          ok: true,
                          ...(warmTtl === null ? {} : { ttlSeconds: warmTtl }),
                        }
                      : { ok: true, warmed: true },
                  },
            ),
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

function warmCount(t: { sent: Array<{ method?: string }> }): number {
  return t.sent.filter((m) => m.method === "warmAccountRulesMemory").length;
}

/** 冻结 `Date.now()` 并手动推进——TTL 以分钟计，测试等不起。 */
function frozenClock() {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  return {
    advance: (ms: number) => {
      now += ms;
    },
  };
}

const accountAuth = {
  baseUrl: "https://api.example.com/v1/account",
  apiKey: "acct-tok",
};

function startTurnReq(
  overrides: Partial<{
    conversationId: string;
    rootId: string;
    turnId: string;
    userMessageId: string;
    folderId: string | null;
    accountAuth: typeof accountAuth;
    userId: string;
  }> = {},
) {
  return {
    conversationId: overrides.conversationId ?? "c1",
    rootId: overrides.rootId ?? "r1",
    turnId: overrides.turnId ?? "turn-1",
    traceId: "a".repeat(32),
    userMessageId: overrides.userMessageId ?? "u1",
    messageId: "m-asst",
    userMessage: "hello",
    folderId: overrides.folderId ?? "folder-1",
    accountAuth: overrides.accountAuth,
    userId: overrides.userId,
  };
}

describe("SidecarManager warmAccountRulesMemory", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));
  afterEach(() => vi.restoreAllMocks());

  it("warmAccountRulesMemory sends RPC with accountAuth + folderId after initialize", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmAccountRulesMemory("r1", "", "/tmp/ws-warm-rules", {
      folderId: "folder-1",
      accountAuth,
      userId: "user-1",
    });

    expect(t.sent.map((m) => m.method)).toEqual([
      "initialize",
      "warmAccountRulesMemory",
    ]);
    const init = t.sent.find((m) => m.method === "initialize");
    expect(init?.params?.userId).toBe("user-1");
    const warm = t.sent.find((m) => m.method === "warmAccountRulesMemory");
    expect(warm?.params).toEqual({
      folderId: "folder-1",
      accountAuth,
      userId: "user-1",
    });
  });

  it("skips RPC when accountAuth absent", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmAccountRulesMemory("r1", "", "/tmp/ws-warm-rules-skip", {
      folderId: "folder-1",
    });

    expect(t.sent.map((m) => m.method)).toEqual(["initialize"]);
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(0);
  });

  it("ensure / probe cache hit does not kick warmAccountRulesMemory", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.probe("r1", "", "/tmp/ws-warm-rules2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(0);

    await manager.probe("r1", "", "/tmp/ws-warm-rules2");
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(0);
  });

  it("first startTurn with accountAuth awaits warm once before startTurn RPC", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    await manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-st",
        turnId: "turn-st-1",
        accountAuth,
        userId: "user-st",
      }),
      "/tmp/ws-st",
    );

    expect(t.sent.map((m) => m.method)).toEqual([
      "initialize",
      "warmAccountRulesMemory",
      "startTurn",
    ]);
    const warm = t.sent.find((m) => m.method === "warmAccountRulesMemory");
    expect(warm?.params).toEqual({
      folderId: "folder-1",
      accountAuth,
      userId: "user-st",
    });

    await manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-st",
        turnId: "turn-st-2",
        userMessageId: "u2",
        accountAuth,
        userId: "user-st",
      }),
      "/tmp/ws-st",
    );

    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(1);
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
    expect(t.sent.filter((m) => m.method === "startTurn").length).toBe(2);
  });

  it("no-auth skip does not lock; late login with auth re-warms", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    await manager.startTurn(
      wc as never,
      startTurnReq({ rootId: "r-noauth", turnId: "t1" }),
      "/tmp/ws-noauth",
    );
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(0);

    await manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-noauth",
        turnId: "t2",
        userMessageId: "u2",
        accountAuth,
        userId: "user-late",
      }),
      "/tmp/ws-noauth",
    );
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(1);
    const warm = t.sent.find((m) => m.method === "warmAccountRulesMemory");
    expect(warm?.params?.userId).toBe("user-late");
  });

  it("re-warms the next turn once the server snapshot TTL lapses", async () => {
    const t = capturingTransport({ warmTtlSeconds: 300 });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };
    const clock = frozenClock();
    const req = (turnId: string) =>
      startTurnReq({
        rootId: "r-ttl",
        turnId,
        userMessageId: turnId,
        accountAuth,
        userId: "user-ttl",
      });

    await manager.startTurn(wc as never, req("t1"), "/tmp/ws-ttl");
    expect(warmCount(t)).toBe(1);

    // 仍在窗口内（300s TTL 减 15s 续期余量）：不叠跑猛踢。
    clock.advance(280_000);
    await manager.startTurn(wc as never, req("t2"), "/tmp/ws-ttl");
    expect(warmCount(t)).toBe(1);

    // 越过窗口：服务端此刻已 miss，回合发 RPC 前必须先续暖。
    clock.advance(10_000);
    await manager.startTurn(wc as never, req("t3"), "/tmp/ws-ttl");
    expect(warmCount(t)).toBe(2);
    const methods = t.sent.map((m) => m.method);
    expect(methods.lastIndexOf("warmAccountRulesMemory")).toBeLessThan(
      methods.lastIndexOf("startTurn"),
    );
    expect(t.sent.filter((m) => m.method === "initialize").length).toBe(1);
  });

  it("tracks freshness per snapshot key (folderId / account)", async () => {
    const t = capturingTransport({ warmTtlSeconds: 300 });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };
    frozenClock();

    await manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-key",
        turnId: "k1",
        folderId: "folder-1",
        accountAuth,
        userId: "user-key",
      }),
      "/tmp/ws-key",
    );
    await manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-key",
        turnId: "k2",
        userMessageId: "u2",
        folderId: "folder-2",
        accountAuth,
        userId: "user-key",
      }),
      "/tmp/ws-key",
    );

    // 服务端缓存键是 (user_id, folder_id)：folder-1 暖过不代表 folder-2 有快照。
    const warms = t.sent.filter((m) => m.method === "warmAccountRulesMemory");
    expect(warms.map((m) => m.params?.folderId)).toEqual([
      "folder-1",
      "folder-2",
    ]);
  });

  it("treats a reply without ttlSeconds as already expired", async () => {
    const t = capturingTransport({ warmTtlSeconds: null });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };
    frozenClock();

    await manager.startTurn(
      wc as never,
      startTurnReq({ rootId: "r-nottl", turnId: "n1", accountAuth }),
      "/tmp/ws-nottl",
    );
    await manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-nottl",
        turnId: "n2",
        userMessageId: "u2",
        accountAuth,
      }),
      "/tmp/ws-nottl",
    );

    // 宁可每回合多暖一次，也不谎报新鲜（谎报＝静默丢规则与长期记忆）。
    expect(warmCount(t)).toBe(2);
  });

  it("backs off after a failed warm RPC instead of locking the entry", async () => {
    const t = capturingTransport({ warmFails: true });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };
    const clock = frozenClock();
    const req = (turnId: string) =>
      startTurnReq({
        rootId: "r-fail",
        turnId,
        userMessageId: turnId,
        accountAuth,
        userId: "user-fail",
      });

    await manager.startTurn(wc as never, req("f1"), "/tmp/ws-fail");
    expect(warmCount(t)).toBe(1);

    // 退避窗口内不猛踢……
    clock.advance(29_000);
    await manager.startTurn(wc as never, req("f2"), "/tmp/ws-fail");
    expect(warmCount(t)).toBe(1);

    // ……但失败不再锁死整个 sidecar 生命周期，退避过后重试。
    clock.advance(2_000);
    await manager.startTurn(wc as never, req("f3"), "/tmp/ws-fail");
    expect(warmCount(t)).toBe(2);
  });

  it("explicit warm keeps the key fresh so a prompt startTurn does not re-kick", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmAccountRulesMemory("r-open", "", "/tmp/ws-open", {
      folderId: "folder-1",
      accountAuth,
      userId: "user-open",
    });
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(1);

    await manager.startTurn(
      { isDestroyed: () => false, send: vi.fn() } as never,
      startTurnReq({
        rootId: "r-open",
        turnId: "turn-after-open",
        accountAuth,
        userId: "user-open",
      }),
      "/tmp/ws-open",
    );
    expect(
      t.sent.filter((m) => m.method === "warmAccountRulesMemory").length,
    ).toBe(1);
  });

  it("force warm / refreshLive re-kicks while the TTL window is still fresh", async () => {
    const t = capturingTransport();
    const manager = new SidecarManager(() => t.transport);

    await manager.warmAccountRulesMemory("r-open", "", "/tmp/ws-open-force", {
      folderId: "folder-1",
      accountAuth,
      userId: "user-open",
    });
    expect(warmCount(t)).toBe(1);

    await manager.warmAccountRulesMemory("r-open", "", "/tmp/ws-open-force", {
      folderId: "folder-1",
      accountAuth,
      userId: "user-open",
    });
    expect(warmCount(t)).toBe(1);

    await manager.warmAccountRulesMemory("r-open", "", "/tmp/ws-open-force", {
      folderId: "folder-1",
      accountAuth,
      userId: "user-open",
      force: true,
    });
    expect(warmCount(t)).toBe(2);

    await manager.refreshLiveAccountRulesMemory({
      accountAuth,
      userId: "user-open",
    });
    expect(warmCount(t)).toBe(3);
  });

  it("startTurn awaits in-flight account warm before startTurn RPC", async () => {
    const t = capturingTransport({ warmDelayMs: 40 });
    const manager = new SidecarManager(() => t.transport);
    const wc = { isDestroyed: () => false, send: vi.fn() };

    const warmP = manager.warmAccountRulesMemory(
      "r-await-rules",
      "",
      "/tmp/ws-await-rules",
      { folderId: "folder-1", accountAuth, userId: "user-await" },
    );
    await vi.waitFor(() => {
      expect(t.sent.some((m) => m.method === "warmAccountRulesMemory")).toBe(
        true,
      );
    });
    expect(t.sent.some((m) => m.method === "startTurn")).toBe(false);

    const turnP = manager.startTurn(
      wc as never,
      startTurnReq({
        rootId: "r-await-rules",
        turnId: "turn-await-rules",
        accountAuth,
        userId: "user-await",
      }),
      "/tmp/ws-await-rules",
    );

    await Promise.all([warmP, turnP]);
    const methods = t.sent.map((m) => m.method);
    const warmIdx = methods.indexOf("warmAccountRulesMemory");
    const turnIdx = methods.indexOf("startTurn");
    expect(warmIdx).toBeGreaterThanOrEqual(0);
    expect(turnIdx).toBeGreaterThan(warmIdx);
  });
});
