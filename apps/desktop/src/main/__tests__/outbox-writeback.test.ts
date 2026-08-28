/**
 * Outbox writebacker: at-least-once drain + idempotent delivery.
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/outbox-wb-test-${Math.random().toString(36).slice(2)}`,
    bearerPostJson: vi.fn(),
  };
});

vi.mock("electron", () => ({
  app: {
    getPath: () => h.dir,
    on: vi.fn(),
  },
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getAllWindows: () => [] },
}));

vi.mock("../auth-client", () => ({
  bearerPostJson: h.bearerPostJson,
  refreshAccessToken: vi.fn(async () => "renewed" as const),
  persistAuthCookies: vi.fn(async () => {}),
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

import {
  EMPTY_USER_MESSAGE_PLACEHOLDER,
  LOCAL_TURN_LEASE_HEARTBEAT_MS,
  computeBackoffDelayMs,
  deadLetterDir,
  drainOutbox,
  fillEmptyUserMessageForWriteback,
  flushTurn,
  isPermanentHttpFailure,
  isSafeOutboxId,
  normalizeToolFailureCode,
  noteOccupiedLocalTurn,
  outboxDir,
  resetLocalTurnProjectionForTests,
  shouldDeleteOutboxAfterAck,
  toRecordTurnBody,
  toolFailuresFromJournal,
} from "../outbox-writeback";

const dir = () => outboxDir();

function writeReady(
  userMessageId: string,
  overrides: Record<string, unknown> = {},
) {
  mkdirSync(dir(), { recursive: true });
  writeFileSync(
    join(dir(), `${userMessageId}.json`),
    JSON.stringify({
      schema_version: 1,
      user_message_id: userMessageId,
      conversation_id: "c1",
      message_id: "m1",
      trace_id: "a".repeat(32),
      user_message: "hello",
      content: "world",
      phase: "ready",
      input_tokens: 1,
      output_tokens: 2,
      reasoning_tokens: 0,
      cache_hit_tokens: 0,
      cache_miss_tokens: 0,
      rounds: 1,
      finish_reason: "stop",
      ...overrides,
    }),
    "utf-8",
  );
}

function readRecord(userMessageId: string): Record<string, unknown> {
  return JSON.parse(
    readFileSync(join(dir(), `${userMessageId}.json`), "utf-8"),
  ) as Record<string, unknown>;
}

describe("drainOutbox", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  beforeEach(() => {
    rmSync(dir(), { recursive: true, force: true });
    rmSync(deadLetterDir(), { recursive: true, force: true });
    h.bearerPostJson.mockReset();
    resetLocalTurnProjectionForTests();
  });

  it("POSTs ready records and deletes on ack (at-least-once)", async () => {
    writeReady("u1");
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u1",
        assistant_message_id: "m1",
        title: "T",
      },
    });

    const status = await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const [path, body] = h.bearerPostJson.mock.calls[0] as [
      string,
      { user_message_id: string; content: string },
    ];
    expect(path).toBe("/v1/conversations/c1/local-turns");
    expect(body.user_message_id).toBe("u1");
    expect(body.content).toBe("world");
    expect(body).not.toHaveProperty("origin");
    expect(status.pending).toEqual([]);
  });

  it("POSTs harvest provenance on RecordTurnRequest (origin / execution_id / harvest_kind)", async () => {
    writeReady("u-harvest", {
      origin: "execution_harvest",
      execution_id: "exec-h1",
      harvest_kind: "completed",
      user_message: "【系统收口】后台团队任务已全部完成。",
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-harvest",
        assistant_message_id: "m-h",
        title: null,
      },
    });
    await drainOutbox();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body.origin).toBe("execution_harvest");
    expect(body.execution_id).toBe("exec-h1");
    expect(body.harvest_kind).toBe("completed");
  });

  it("encodes conversation_id in writeback URL", async () => {
    writeReady("u-enc", { conversation_id: "c with space" });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-enc",
        assistant_message_id: "m1",
        title: null,
      },
    });
    await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const [path] = h.bearerPostJson.mock.calls[0] as [string];
    expect(path).toBe(
      `/v1/conversations/${encodeURIComponent("c with space")}/local-turns`,
    );
  });

  it("skips records whose conversation_id is unsafe (no POST)", async () => {
    writeReady("u-bad-conv", { conversation_id: "../escape" });
    const status = await drainOutbox();
    expect(h.bearerPostJson).not.toHaveBeenCalled();
    // Unsafe ids are filtered at read — not surfaced as pending either.
    expect(status.pending).toEqual([]);
  });

  it("skips planted files whose user_message_id is unsafe", async () => {
    mkdirSync(dir(), { recursive: true });
    writeFileSync(
      join(dir(), "planted.json"),
      JSON.stringify({
        schema_version: 1,
        user_message_id: "a/b",
        conversation_id: "c1",
        trace_id: "a".repeat(32),
        user_message: "hello",
        content: "world",
        phase: "ready",
      }),
      "utf-8",
    );
    const status = await drainOutbox();
    expect(h.bearerPostJson).not.toHaveBeenCalled();
    expect(status.pending).toEqual([]);
    // File left untouched — we do not path-join unsafe ids.
    expect(existsSync(join(dir(), "planted.json"))).toBe(true);
  });

  it("skips unique pid-seq tmp leftovers when scanning", async () => {
    writeReady("u-tmp-skip");
    writeFileSync(
      join(dir(), `u-tmp-skip.json.${process.pid}-99.tmp`),
      "{not-valid-json",
      "utf-8",
    );
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-tmp-skip",
        assistant_message_id: "m1",
        title: null,
      },
    });
    const status = await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    expect(status.pending).toEqual([]);
    // Tmp leftover remains; must not break drain or be treated as a record.
    expect(
      existsSync(join(dir(), `u-tmp-skip.json.${process.pid}-99.tmp`)),
    ).toBe(true);
  });

  it("retry write leaves no shared .json.tmp (unique pid-seq temps)", async () => {
    writeReady("u-tmp-unique");
    h.bearerPostJson.mockResolvedValueOnce({
      ok: false,
      status: 503,
      body: { error: "busy" },
    });
    await drainOutbox();
    const names = readdirSync(dir());
    expect(names.some((n) => n.endsWith(".json.tmp"))).toBe(false);
    expect(names).toContain("u-tmp-unique.json");
    expect(names.some((n) => n.includes(".tmp"))).toBe(false);
  });

  it("leaves the file when POST fails (retry later) and stamps backoff", async () => {
    writeReady("u2");
    h.bearerPostJson.mockResolvedValueOnce({
      ok: false,
      status: 503,
      body: { error: "busy" },
    });
    const status = await drainOutbox();
    expect(status.pending).toHaveLength(1);
    expect(status.pending[0]?.userMessageId).toBe("u2");
    const onDisk = readRecord("u2");
    expect(onDisk.retry_count).toBe(1);
    expect(typeof onDisk.next_attempt_at).toBe("number");
    expect(onDisk.next_attempt_at as number).toBeGreaterThan(Date.now());
  });

  it("moves permanent 4xx (e.g. 404) to dead-letter and drops from pending", async () => {
    writeReady("u-404");
    h.bearerPostJson.mockResolvedValueOnce({
      ok: false,
      status: 404,
      body: { error: "not_found" },
    });
    const status = await drainOutbox();
    expect(status.pending).toEqual([]);
    expect(existsSync(join(dir(), "u-404.json"))).toBe(false);
    expect(existsSync(join(deadLetterDir(), "u-404.json"))).toBe(true);
  });

  it("skips records still within backoff window", async () => {
    writeReady("u-backoff", {
      retry_count: 2,
      next_attempt_at: Date.now() + 60_000,
    });
    const status = await drainOutbox();
    expect(h.bearerPostJson).not.toHaveBeenCalled();
    expect(status.pending).toHaveLength(1);
    expect(status.pending[0]?.userMessageId).toBe("u-backoff");
  });

  it("retries when next_attempt_at has elapsed", async () => {
    writeReady("u-due", {
      retry_count: 1,
      next_attempt_at: Date.now() - 1_000,
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-due",
        assistant_message_id: "m1",
        title: null,
      },
    });
    const status = await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    expect(status.pending).toEqual([]);
  });

  it("flushTurn bypasses backoff and attempts immediately", async () => {
    writeReady("u-flush", {
      retry_count: 3,
      next_attempt_at: Date.now() + 60_000,
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-flush",
        assistant_message_id: "m1",
        title: null,
      },
    });
    const result = await flushTurn("u-flush");
    expect(result.ok).toBe(true);
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
  });

  it("open records POST journal/segments, never local-turns", async () => {
    writeReady("u3", {
      phase: "open",
      content: "partial",
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ts: "t0" },
      },
      stream_segments: {
        "captain:content": { text: "partial", generation: 0 },
      },
    });
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {},
    });
    const status = await drainOutbox();
    const paths = h.bearerPostJson.mock.calls.map((c) => String(c[0]));
    expect(paths).not.toContain("/v1/conversations/c1/local-turns/begin");
    expect(paths).toContain("/v1/conversations/c1/local-turns/journal");
    expect(paths).toContain("/v1/conversations/c1/local-turns/stream-segments");
    expect(paths).not.toContain("/v1/conversations/c1/local-turns");
    const journalBody = h.bearerPostJson.mock.calls.find((c) =>
      String(c[0]).endsWith("/journal"),
    )?.[1] as { replace?: boolean };
    expect(journalBody.replace).toBe(false);
    expect(status.pending).toHaveLength(1);
    expect(status.pending[0]?.phase).toBe("open");
  });

  it("missing occupied file does not abort on drain (startTurn owns occupy)", async () => {
    noteOccupiedLocalTurn("u-gone", {
      conversationId: "c1",
      messageId: "m1",
    });
    await drainOutbox();
    expect(h.bearerPostJson).not.toHaveBeenCalled();
  });

  it("ready + non-empty user_message POSTs original text (C2 no mis-fire)", async () => {
    writeReady("u-normal", {
      user_message: "real user text",
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ts: "t0" },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-normal",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      user_message?: string;
    };
    expect(body.user_message).toBe("real user text");
    expect(body.user_message).not.toBe(EMPTY_USER_MESSAGE_PLACEHOLDER);
  });

  it("ready + empty user_message + journal POSTs empty um (C2, no placeholder bubble)", async () => {
    writeReady("u-empty-um", {
      user_message: "",
      message_id: "m-assist",
      content: "",
      runs: null,
      finish_reason: "cancelled",
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ts: "t0" },
        "1": { kind: "run_completed", payload: { id: "r1" }, ts: null },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-empty-um",
        assistant_message_id: "m-assist",
        title: null,
      },
    });

    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const status = await drainOutbox();
      expect(h.bearerPostJson).toHaveBeenCalledOnce();
      const body = h.bearerPostJson.mock.calls[0]?.[1] as {
        user_message?: string;
        journal?: unknown[];
        message_id?: string | null;
      };
      expect(body.user_message).toBe("");
      expect(body.user_message).not.toBe(EMPTY_USER_MESSAGE_PLACEHOLDER);
      expect(body.message_id).toBe("m-assist");
      expect(Array.isArray(body.journal) && body.journal.length).toBe(2);
      expect(status.pending).toEqual([]);
      expect(existsSync(join(dir(), "u-empty-um.json"))).toBe(false);
      expect(
        warnSpy.mock.calls.some(
          (c) =>
            typeof c[0] === "string" &&
            c[0].includes("empty user_message → writeback without user bubble"),
        ),
      ).toBe(true);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it("ready + legacy placeholder um + journal normalizes to empty POST (C2)", async () => {
    writeReady("u-legacy-ph", {
      user_message: EMPTY_USER_MESSAGE_PLACEHOLDER,
      message_id: "m-legacy",
      content: "",
      runs: null,
      finish_reason: "cancelled",
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ts: "t0" },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-legacy-ph",
        assistant_message_id: "m-legacy",
        title: null,
      },
    });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      await drainOutbox();
      expect(h.bearerPostJson).toHaveBeenCalledOnce();
      const body = h.bearerPostJson.mock.calls[0]?.[1] as {
        user_message?: string;
      };
      expect(body.user_message).toBe("");
      expect(body.user_message).not.toBe(EMPTY_USER_MESSAGE_PLACEHOLDER);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it("ready + empty user_message without process dead-letters with log (C2)", async () => {
    writeReady("u-empty-noproc", {
      user_message: "",
      content: "",
      runs: null,
      journal: undefined,
      reasoning_content: null,
    });
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const status = await drainOutbox();
      expect(h.bearerPostJson).not.toHaveBeenCalled();
      expect(existsSync(join(dir(), "u-empty-noproc.json"))).toBe(false);
      expect(existsSync(join(deadLetterDir(), "u-empty-noproc.json"))).toBe(
        true,
      );
      expect(status.pending).toEqual([]);
      expect(
        errSpy.mock.calls.some(
          (c) =>
            typeof c[0] === "string" &&
            c[0].includes("skip empty user_message (not postable)"),
        ),
      ).toBe(true);
    } finally {
      errSpy.mockRestore();
    }
  });

  it("fillEmptyUserMessageForWriteback gates on process; never fills placeholder", () => {
    const withJournal = {
      user_message_id: "u1",
      conversation_id: "c1",
      user_message: "",
      journal: { "0": { kind: "run_started", payload: {} } },
    };
    expect(fillEmptyUserMessageForWriteback(withJournal)).toBe(true);
    expect(withJournal.user_message).toBe("");

    const legacy = {
      user_message_id: "u-legacy",
      conversation_id: "c1",
      user_message: EMPTY_USER_MESSAGE_PLACEHOLDER,
      journal: { "0": { kind: "run_started", payload: {} } },
    };
    expect(fillEmptyUserMessageForWriteback(legacy)).toBe(true);
    expect(legacy.user_message).toBe("");

    const empty = {
      user_message_id: "u2",
      conversation_id: "c1",
      user_message: "",
    };
    expect(fillEmptyUserMessageForWriteback(empty)).toBe(false);
    expect(empty.user_message).toBe("");
  });

  it("includes seq-sorted journal on writeback when ord is missing (legacy crash salvage)", async () => {
    // No ``ord``: keep the old integer-key order. Overflow+resume emission
    // order is the twin test below (``ord`` on each entry).
    writeReady("u-j", {
      runs: null,
      finish_reason: "cancelled",
      journal: {
        "2": { kind: "run_completed", payload: { id: "r1" }, ts: null },
        "0": { kind: "run_started", payload: { id: "r1" }, ts: "t0" },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-j",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      journal?: unknown[];
      finish_reason?: string;
      runs?: unknown;
    };
    expect(body.runs).toBeNull();
    expect(body.finish_reason).toBe("cancelled");
    expect(body.journal).toEqual([
      { kind: "run_started", payload: { id: "r1" }, ts: "t0" },
      { kind: "run_completed", payload: { id: "r1" }, ts: null },
    ]);
  });

  it("writeback journal is emission order after pause overflow and resume live", async () => {
    // Twin of server test_journal_overflow_seq_projection: seal prefix →
    // overflow terminals → resume live tail. Integer-like keys reorder under
    // JSON.parse / JS index order; ``ord`` is the shared order fact.
    const overflow = 1_000_000;
    writeReady("u-ord", {
      runs: null,
      finish_reason: "cancelled",
      journal: {
        "0": { kind: "run_plan", payload: {}, ts: "t0", ord: 0 },
        "1": { kind: "run_started", payload: { id: "r1" }, ts: "t1", ord: 1 },
        "2": { kind: "checkpoint_required", payload: {}, ts: "t2", ord: 2 },
        [String(overflow)]: {
          kind: "run_completed",
          payload: { id: "r1" },
          ts: "t-ov",
          ord: 3,
        },
        [String(overflow + 1)]: {
          kind: "execution_completed",
          payload: {},
          ts: "t-ov2",
          ord: 4,
        },
        "3": {
          kind: "checkpoint_resolved",
          payload: {},
          ts: "t5",
          ord: 5,
        },
        "4": { kind: "run_started", payload: { id: "r2" }, ts: "t6", ord: 6 },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-ord",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      journal?: Array<{
        kind?: string;
        payload?: { id?: string };
        ts?: string;
      }>;
    };
    expect(body.journal).toEqual([
      { kind: "run_plan", payload: {}, ts: "t0" },
      { kind: "run_started", payload: { id: "r1" }, ts: "t1" },
      { kind: "checkpoint_required", payload: {}, ts: "t2" },
      { kind: "run_completed", payload: { id: "r1" }, ts: "t-ov" },
      { kind: "execution_completed", payload: {}, ts: "t-ov2" },
      { kind: "checkpoint_resolved", payload: {}, ts: "t5" },
      { kind: "run_started", payload: { id: "r2" }, ts: "t6" },
    ]);
  });

  it("includes tool_failures from journal tool_call success=false", async () => {
    writeReady("u-tf", {
      journal: {
        "0": {
          kind: "tool_call",
          payload: {
            name: "web_search",
            success: false,
            result: "搜索失败：无法建立连接（出网受限或站点不可达）",
          },
          ts: "t0",
        },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-tf",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await drainOutbox();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      tool_failures?: Array<{ tool: string; code: string; message: string }>;
    };
    expect(body.tool_failures).toEqual([
      {
        tool: "web_search",
        code: "egress_connect",
        message: "搜索失败：无法建立连接（出网受限或站点不可达）",
      },
    ]);
  });

  it("omits tool_failures when journal has no failed tools", async () => {
    writeReady("u-ok", {
      journal: {
        "0": {
          kind: "tool_call",
          payload: { name: "web_search", success: true, result: "ok" },
        },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-ok",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await drainOutbox();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body.tool_failures).toBeUndefined();
  });

  it("normalizeToolFailureCode maps searxng / egress / declaration / other", () => {
    expect(normalizeToolFailureCode("searxng unreachable")).toBe(
      "searxng_unreachable",
    );
    expect(normalizeToolFailureCode("无法建立连接（出网受限）")).toBe(
      "egress_connect",
    );
    expect(
      normalizeToolFailureCode(
        "delegate 缺 tasks/playbook：请在 payload 顶层直接放非空 `tasks`",
      ),
    ).toBe("declaration_empty");
    expect(
      normalizeToolFailureCode("playbook 与 tasks 二选一，不可同时传。…"),
    ).toBe("declaration_xor");
    expect(normalizeToolFailureCode("未知 playbook『x』；可用：a。")).toBe(
      "declaration_unknown",
    );
    expect(normalizeToolFailureCode("缺少参数")).toBe("other");
    expect(normalizeToolFailureCode("缺少必填参数：query")).toBe("schema");
    expect(normalizeToolFailureCode("x", "egress_connect")).toBe(
      "egress_connect",
    );
    expect(normalizeToolFailureCode("x", "declaration_empty")).toBe(
      "declaration_empty",
    );
    expect(normalizeToolFailureCode("x", "git_timeout")).toBe("git_timeout");
    expect(normalizeToolFailureCode("x", "timeout")).toBe("git_timeout");
    expect(normalizeToolFailureCode("x", "no_repo")).toBe("no_repo");
    expect(normalizeToolFailureCode("x", "schema")).toBe("schema");
    expect(normalizeToolFailureCode("x", "not_a_web_url")).toBe(
      "not_a_web_url",
    );
    expect(normalizeToolFailureCode("x", "url_not_workspace_path")).toBe(
      "url_not_workspace_path",
    );
    expect(normalizeToolFailureCode("文件不存在：docs/ghost.md")).toBe(
      "not_found",
    );
    expect(
      normalizeToolFailureCode("不是目录：apps/server/src", "schema"),
    ).toBe("not_found");
    expect(normalizeToolFailureCode("x", "not_found")).toBe("not_found");
    expect(normalizeToolFailureCode("[WinError 5] 拒绝访问", "other")).toBe(
      "access_denied",
    );
    expect(
      normalizeToolFailureCode(
        "写入被占用（杀毒/索引/其他程序正打开该文件），不是没授权",
      ),
    ).toBe("access_denied");
    expect(
      normalizeToolFailureCode(
        "路径 '../escaped.md' 超出了工作区范围。请使用工作区相对路径",
        "other",
      ),
    ).toBe("outside_workspace");
    expect(
      normalizeToolFailureCode(
        "禁止用 code_execute 跑项目级慢验证（检测到：pytest）。",
      ),
    ).toBe("project_verify_redirect");
    expect(normalizeToolFailureCode("x", "project_verify_redirect")).toBe(
      "project_verify_redirect",
    );
    expect(
      normalizeToolFailureCode(
        "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
      ),
    ).toBe("source_grep_redirect");
  });

  it("toolFailuresFromJournal prefers tool_call over tool_use_end", () => {
    expect(
      toolFailuresFromJournal([
        {
          kind: "tool_call",
          payload: {
            name: "web_search",
            success: false,
            result: "searxng down",
          },
        },
        {
          kind: "tool_use_end",
          payload: {
            tool_name: "web_search",
            status: "error",
            result: "display duplicate",
          },
        },
      ]),
    ).toEqual([
      {
        tool: "web_search",
        code: "searxng_unreachable",
        message: "searxng down",
      },
    ]);
  });

  it("toolFailuresFromJournal passes payload.code", () => {
    expect(
      toolFailuresFromJournal([
        {
          kind: "tool_call",
          payload: {
            name: "git",
            success: false,
            result: "工作区无 git 仓库",
            code: "no_repo",
          },
        },
      ]),
    ).toEqual([
      {
        tool: "git",
        code: "no_repo",
        message: "工作区无 git 仓库",
      },
    ]);
  });

  it("toolFailuresFromJournal omits channel redirects", () => {
    expect(
      toolFailuresFromJournal([
        {
          kind: "tool_call",
          payload: {
            name: "code_execute",
            success: false,
            result:
              "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
            code: "source_grep_redirect",
          },
        },
      ]),
    ).toEqual([]);
  });

  it("toolFailuresFromJournal maps 缺少必填参数 to schema", () => {
    expect(
      toolFailuresFromJournal([
        {
          kind: "tool_call",
          payload: {
            name: "code_search",
            success: false,
            result: "缺少必填参数：query",
          },
        },
      ]),
    ).toEqual([
      {
        tool: "code_search",
        code: "schema",
        message: "缺少必填参数：query",
      },
    ]);
  });

  it("salvageOpen promotes settled open rows as cancelled (no retain-open)", async () => {
    const { recoverLocalPersistence } = await import("../outbox-writeback");
    writeReady("u-retain", {
      phase: "open",
      content: "partial after kickoff",
      finish_reason: null,
      journal: {
        "0": {
          kind: "team_preview_required",
          payload: { checkpoint_id: "tp1" },
        },
        "1": {
          kind: "team_preview_resolved",
          payload: {
            checkpoint_id: "tp1",
            decision: "continue",
            resume_frame: {
              frame: { kind: "team_preview", checkpoint_id: "tp1" },
            },
          },
        },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-retain",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await recoverLocalPersistence();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      finish_reason?: string;
    };
    expect(body.finish_reason).toBe("cancelled");
    expect(existsSync(join(dir(), "u-retain.json"))).toBe(false);
  });

  it("salvageOpen promotes journal-only open rows (no content text)", async () => {
    const { recoverLocalPersistence } = await import("../outbox-writeback");
    writeReady("u-journal-only", {
      phase: "open",
      content: "",
      finish_reason: null,
      journal: {
        "0": {
          kind: "team_preview_resolved",
          payload: {
            checkpoint_id: "tp1",
            decision: "continue",
            resume_frame: { frame: { kind: "team_preview" } },
          },
        },
        "1": { kind: "run_started", payload: { id: "r1" } },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-journal-only",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await recoverLocalPersistence();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      finish_reason?: string;
      journal?: unknown[];
    };
    expect(body.finish_reason).toBe("cancelled");
    expect(Array.isArray(body.journal) && body.journal.length).toBeGreaterThan(
      0,
    );
    expect(existsSync(join(dir(), "u-journal-only.json"))).toBe(false);
  });

  it("ready terminal turns with settlement still writeback", async () => {
    writeReady("u-done", {
      phase: "ready",
      finish_reason: "end_turn",
      journal: {
        "0": {
          kind: "team_preview_resolved",
          payload: {
            checkpoint_id: "tp1",
            decision: "continue",
            resume_frame: { frame: { kind: "team_preview" } },
          },
        },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-done",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    expect(existsSync(join(dir(), "u-done.json"))).toBe(false);
  });

  it("salvageOpen promotes abandoned open rows as cancelled (not error)", async () => {
    const { recoverLocalPersistence } = await import("../outbox-writeback");
    writeReady("u-open", {
      phase: "open",
      content: "partial",
      finish_reason: null,
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-open",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await recoverLocalPersistence();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      finish_reason?: string;
    };
    expect(body.finish_reason).toBe("cancelled");
  });

  it("salvageOpen promotes open rows with empty content from captain stream_segments", async () => {
    const { recoverLocalPersistence } = await import("../outbox-writeback");
    writeReady("u-snap", {
      phase: "open",
      content: "",
      reasoning_content: null,
      finish_reason: null,
      stream_segments: {
        "captain:content": { text: "half reply from flush", generation: 0 },
        "captain:reasoning": { text: "mid think", generation: 0 },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-snap",
        assistant_message_id: "m1",
        title: null,
      },
    });

    await recoverLocalPersistence();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      content?: string;
      reasoning_content?: string | null;
      finish_reason?: string;
    };
    expect(body.content).toBe("half reply from flush");
    expect(body.reasoning_content).toBe("mid think");
    expect(body.finish_reason).toBe("cancelled");
  });

  it("regular drain POSTs stream-segments for open rows, not local-turns", async () => {
    writeReady("u-open-segs", {
      phase: "open",
      content: "",
      stream_segments: {
        "captain:content": {
          text: "should not promote mid-turn",
          generation: 0,
        },
      },
    });
    h.bearerPostJson.mockResolvedValue({
      ok: true,
      status: 200,
      body: {},
    });
    const status = await drainOutbox();
    const paths = h.bearerPostJson.mock.calls.map((c) => String(c[0]));
    expect(paths).toContain("/v1/conversations/c1/local-turns/stream-segments");
    expect(paths).not.toContain("/v1/conversations/c1/local-turns");
    expect(status.pending).toHaveLength(1);
    expect(status.pending[0]?.phase).toBe("open");
  });

  it("salvageOpen discards begin-only empty open shells (no um / no body)", async () => {
    const { recoverLocalPersistence } = await import("../outbox-writeback");
    writeReady("u-empty-shell", {
      phase: "open",
      user_message: "",
      content: "",
      finish_reason: null,
      runs: null,
      journal: undefined,
      reasoning_content: null,
      stream_segments: undefined,
      ops: ["begin_turn"],
    });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      await recoverLocalPersistence();
      expect(h.bearerPostJson).toHaveBeenCalledWith(
        "/v1/conversations/c1/local-turns/abort",
        { user_message_id: "u-empty-shell", message_id: "m1" },
      );
      expect(existsSync(join(dir(), "u-empty-shell.json"))).toBe(false);
      expect(
        warnSpy.mock.calls.some(
          (c) =>
            typeof c[0] === "string" &&
            c[0].includes("discard empty open shell"),
        ),
      ).toBe(true);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it("salvageOpen seals user_message-only open shells as cancelled writeback", async () => {
    const { recoverLocalPersistence } = await import("../outbox-writeback");
    writeReady("u-um-only", {
      phase: "open",
      user_message: "hello before crash",
      content: "",
      finish_reason: null,
      runs: null,
      journal: undefined,
      reasoning_content: null,
      stream_segments: undefined,
      ops: ["begin_turn"],
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-um-only",
        assistant_message_id: null,
        title: null,
        noop: true,
      },
    });
    await recoverLocalPersistence();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      user_message?: string;
      content?: string;
      finish_reason?: string;
    };
    expect(body.user_message).toBe("hello before crash");
    expect(body.content).toBe("");
    expect(body.finish_reason).toBe("cancelled");
    expect(existsSync(join(dir(), "u-um-only.json"))).toBe(false);
  });

  it("ready drain fills captain stream_segments into POST body", async () => {
    writeReady("u-ready-segs", {
      content: "",
      reasoning_content: null,
      stream_segments: {
        "captain:content": { text: "from segments", generation: 0 },
        "captain:reasoning": { text: "think", generation: 0 },
      },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-ready-segs",
        assistant_message_id: "m1",
        title: null,
        noop: false,
      },
    });
    await drainOutbox();
    expect(h.bearerPostJson).toHaveBeenCalledOnce();
    const body = h.bearerPostJson.mock.calls[0]?.[1] as {
      content?: string;
      reasoning_content?: string | null;
    };
    expect(body.content).toBe("from segments");
    expect(body.reasoning_content).toBe("think");
  });

  it("false ack (null assistant + process) does not delete — dead-letters", async () => {
    writeReady("u-false", {
      content: "",
      runs: { events: [], finish_reason: "end_turn", process: [] },
    });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-false",
        assistant_message_id: null,
        title: null,
        noop: false,
      },
    });
    const status = await drainOutbox();
    expect(existsSync(join(dir(), "u-false.json"))).toBe(false);
    expect(existsSync(join(deadLetterDir(), "u-false.json"))).toBe(true);
    expect(status.pending).toEqual([]);
  });

  it("explicit noop ack deletes empty true-no-op without process", async () => {
    writeReady("u-noop", { content: "", runs: null, journal: undefined });
    h.bearerPostJson.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        user_message_id: "u-noop",
        assistant_message_id: null,
        title: null,
        noop: true,
      },
    });
    await drainOutbox();
    expect(existsSync(join(dir(), "u-noop.json"))).toBe(false);
    expect(existsSync(join(deadLetterDir(), "u-noop.json"))).toBe(false);
  });

  it("listUnsyncedSummaries includes dead-letter as phase=dead", async () => {
    const { listUnsyncedSummaries } = await import("../outbox-writeback");
    mkdirSync(deadLetterDir(), { recursive: true });
    writeFileSync(
      join(deadLetterDir(), "u-dead.json"),
      JSON.stringify({
        schema_version: 1,
        user_message_id: "u-dead",
        conversation_id: "c1",
        message_id: "m-dead",
        trace_id: "a".repeat(32),
        user_message: "hello",
        content: "stuck body",
        phase: "ready",
        updated_at: 100,
        finish_reason: "end_turn",
      }),
      "utf-8",
    );
    const unsynced = await listUnsyncedSummaries("c1");
    expect(unsynced).toHaveLength(1);
    expect(unsynced[0]?.user_message_id).toBe("u-dead");
    expect(unsynced[0]?.phase).toBe("dead");
    expect(unsynced[0]?.content).toBe("stuck body");
  });
});

describe("writeback failure classification", () => {
  it("classifies permanent vs transient HTTP statuses", () => {
    expect(isPermanentHttpFailure(404)).toBe(true);
    expect(isPermanentHttpFailure(400)).toBe(true);
    expect(isPermanentHttpFailure(403)).toBe(true);
    expect(isPermanentHttpFailure(422)).toBe(true);
    expect(isPermanentHttpFailure(401)).toBe(false);
    expect(isPermanentHttpFailure(408)).toBe(false);
    expect(isPermanentHttpFailure(429)).toBe(false);
    expect(isPermanentHttpFailure(500)).toBe(false);
    expect(isPermanentHttpFailure(503)).toBe(false);
    expect(isPermanentHttpFailure(0)).toBe(false);
  });

  it("uses 2s base, doubles, caps at 5 min (jitter injectable)", () => {
    const noJitter = () => 0;
    expect(computeBackoffDelayMs(1, noJitter)).toBe(2_000);
    expect(computeBackoffDelayMs(2, noJitter)).toBe(4_000);
    expect(computeBackoffDelayMs(3, noJitter)).toBe(8_000);
    expect(computeBackoffDelayMs(10, noJitter)).toBe(5 * 60_000);
  });

  it("shouldDeleteOutboxAfterAck requires assistant id, noop, or legacy empty", () => {
    const withRuns = {
      user_message_id: "u",
      conversation_id: "c",
      runs: { events: [] },
    };
    const empty = {
      user_message_id: "u",
      conversation_id: "c",
    };
    expect(
      shouldDeleteOutboxAfterAck(
        { assistant_message_id: "m1", noop: false },
        withRuns,
      ),
    ).toBe(true);
    expect(
      shouldDeleteOutboxAfterAck(
        { assistant_message_id: null, noop: true },
        empty,
      ),
    ).toBe(true);
    expect(
      shouldDeleteOutboxAfterAck(
        { assistant_message_id: null, noop: false },
        withRuns,
      ),
    ).toBe(false);
    expect(
      shouldDeleteOutboxAfterAck(
        { assistant_message_id: null, noop: false },
        empty,
      ),
    ).toBe(true);
  });
});

describe("toRecordTurnBody", () => {
  it("forwards harvest origin / execution_id / harvest_kind when present", () => {
    const body = toRecordTurnBody({
      user_message_id: "u-h",
      conversation_id: "c1",
      user_message: "【系统收口】后台团队任务已全部完成。",
      content: "综合稿",
      trace_id: "a".repeat(32),
      origin: "execution_harvest",
      execution_id: "exec-h1",
      harvest_kind: "completed",
    });
    expect(body.origin).toBe("execution_harvest");
    expect(body.execution_id).toBe("exec-h1");
    expect(body.harvest_kind).toBe("completed");
    expect(body.user_message_id).toBe("u-h");
  });

  it("omits provenance on ordinary turns (no free-text guess)", () => {
    const body = toRecordTurnBody({
      user_message_id: "u1",
      conversation_id: "c1",
      user_message: "hello",
      content: "world",
      trace_id: "a".repeat(32),
    });
    expect(body).not.toHaveProperty("origin");
    expect(body).not.toHaveProperty("execution_id");
    expect(body).not.toHaveProperty("harvest_kind");
    expect(body).not.toHaveProperty("agent_mentions");
  });

  it("forwards agent_mentions when present", () => {
    const mentions = [{ agent_id: "w1", role: "研究员" }];
    const body = toRecordTurnBody({
      user_message_id: "u-m",
      conversation_id: "c1",
      user_message: "hi",
      content: "ok",
      trace_id: "a".repeat(32),
      agent_mentions: mentions,
    });
    expect(body.agent_mentions).toEqual(mentions);
  });

  it("resume_after_seq filters tool_failures but keeps the full journal", () => {
    const body = toRecordTurnBody({
      user_message_id: "u-r",
      conversation_id: "c1",
      user_message: "hi",
      content: "ok",
      trace_id: "a".repeat(32),
      resume_after_seq: 0,
      journal: {
        "0": {
          kind: "tool_call",
          payload: {
            name: "file_read",
            success: false,
            result: "pause fail",
            code: "too_large",
          },
        },
        "1": {
          kind: "tool_call",
          payload: {
            name: "web_search",
            success: false,
            result: "搜索服务 down",
          },
        },
      },
    });
    expect(body.journal).toHaveLength(2);
    expect(body.tool_failures).toEqual([
      {
        tool: "web_search",
        code: "searxng_unreachable",
        message: "搜索服务 down",
      },
    ]);
  });
});

describe("isSafeOutboxId (F3)", () => {
  it("accepts normal UUID and simple ids", () => {
    expect(isSafeOutboxId("550e8400-e29b-41d4-a716-446655440000")).toBe(true);
    expect(isSafeOutboxId("u1")).toBe(true);
    expect(isSafeOutboxId("c with space")).toBe(true);
  });

  it("rejects empty, traversal, separators, NUL", () => {
    expect(isSafeOutboxId("")).toBe(false);
    expect(isSafeOutboxId("../escape")).toBe(false);
    expect(isSafeOutboxId("..\\escape")).toBe(false);
    expect(isSafeOutboxId("foo..bar")).toBe(false);
    expect(isSafeOutboxId("a/b")).toBe(false);
    expect(isSafeOutboxId("a\\b")).toBe(false);
    expect(isSafeOutboxId("a\0b")).toBe(false);
  });
});

describe("local-turn lease heartbeat", () => {
  beforeEach(() => {
    h.bearerPostJson.mockReset();
    resetLocalTurnProjectionForTests();
  });

  it("POSTs heartbeat while occupied and stops after settle", async () => {
    vi.useFakeTimers();
    try {
      h.bearerPostJson.mockResolvedValue({
        ok: true,
        status: 200,
        body: { ok: true },
      });
      noteOccupiedLocalTurn("u-hb", {
        conversationId: "c1",
        messageId: "22222222-2222-4222-8222-222222222222",
      });
      expect(h.bearerPostJson).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(LOCAL_TURN_LEASE_HEARTBEAT_MS);
      expect(h.bearerPostJson).toHaveBeenCalledWith(
        "/v1/conversations/c1/local-turns/heartbeat",
        { message_id: "22222222-2222-4222-8222-222222222222" },
      );
      resetLocalTurnProjectionForTests();
      h.bearerPostJson.mockClear();
      await vi.advanceTimersByTimeAsync(LOCAL_TURN_LEASE_HEARTBEAT_MS);
      expect(h.bearerPostJson).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
      resetLocalTurnProjectionForTests();
    }
  });
});
