/**
 * Mid-turn outbox projection: unacked journal + backoff, not full-file POST.
 * @vitest-environment node
 */
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  return {
    dir: `${base}/outbox-proj-test-${Math.random().toString(36).slice(2)}`,
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

import { logDesktop } from "../log-service";
import type { OutboxRecord } from "../outbox-writeback";
import {
  checkpointOpenRecord,
  outboxDir,
  resetLocalTurnProjectionForTests,
} from "../outbox-writeback";
import {
  JOURNAL_OVERFLOW_SEQ_START,
  journalAckAfterPost,
  unackedJournalEntries,
} from "../outbox/strategy";

const dir = () => outboxDir();

function openRecord(
  userMessageId: string,
  extra: Partial<OutboxRecord> = {},
): OutboxRecord {
  return {
    schema_version: 1,
    user_message_id: userMessageId,
    conversation_id: "c1",
    message_id: "m1",
    trace_id: "a".repeat(32),
    user_message: "hello",
    content: "",
    phase: "open",
    ...extra,
  };
}

function writeOpen(userMessageId: string, extra: Record<string, unknown> = {}) {
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
      content: "",
      phase: "open",
      ...extra,
    }),
    "utf-8",
  );
}

function journalCalls(): unknown[] {
  return h.bearerPostJson.mock.calls
    .filter((c) => String(c[0]).includes("/journal"))
    .map((c) => c[1]);
}

function segmentCalls(): unknown[] {
  return h.bearerPostJson.mock.calls
    .filter((c) => String(c[0]).includes("/stream-segments"))
    .map((c) => c[1]);
}

function journalFailLogs(): Array<{
  level?: string;
  fields?: { repeat?: boolean };
}> {
  return vi
    .mocked(logDesktop)
    .mock.calls.map(
      (c) =>
        c[0] as {
          event?: string;
          level?: string;
          fields?: { repeat?: boolean };
        },
    )
    .filter((e) => e.event === "outbox.local_turn_journal_failed");
}

describe("unackedJournalEntries", () => {
  const row = (seq: number) => ({ seq, entry: { k: seq } });

  it("tracks live and overflow seqs on separate watermarks", () => {
    const overflow = JOURNAL_OVERFLOW_SEQ_START;
    const posted = journalAckAfterPost(
      [row(0), row(overflow)],
      -1,
      overflow - 1,
    );
    expect(posted).toEqual({
      ackedLiveSeq: 0,
      ackedOverflowSeq: overflow,
    });
    expect(
      unackedJournalEntries(
        [row(0), row(1), row(overflow), row(overflow + 1)],
        posted.ackedLiveSeq,
        posted.ackedOverflowSeq,
      ).map((r) => r.seq),
    ).toEqual([1, overflow + 1]);
  });
});

describe("checkpointOpenRecord", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  beforeEach(() => {
    rmSync(dir(), { recursive: true, force: true });
    h.bearerPostJson.mockReset();
    vi.mocked(logDesktop).mockReset();
    resetLocalTurnProjectionForTests();
  });

  afterEach(() => {
    resetLocalTurnProjectionForTests();
    vi.useRealTimers();
  });

  it("POSTs only newly appended journal seqs on the next checkpoint", async () => {
    h.bearerPostJson.mockResolvedValue({ ok: true, status: 200, body: {} });
    await checkpointOpenRecord(
      openRecord("u-incr", {
        journal: {
          "0": { kind: "run_started", payload: { id: "r1" }, ord: 0 },
        },
      }),
    );
    expect(journalCalls()).toEqual([
      {
        message_id: "m1",
        replace: false,
        entries: [
          { seq: 0, entry: { kind: "run_started", payload: { id: "r1" } } },
        ],
      },
    ]);
    h.bearerPostJson.mockClear();
    await checkpointOpenRecord(
      openRecord("u-incr", {
        journal: {
          "0": { kind: "run_started", payload: { id: "r1" }, ord: 0 },
          "1": { kind: "text_delta", payload: { t: "a" }, ord: 1 },
        },
      }),
    );
    expect(journalCalls()).toEqual([
      {
        message_id: "m1",
        replace: false,
        entries: [
          { seq: 1, entry: { kind: "text_delta", payload: { t: "a" } } },
        ],
      },
    ]);
  });

  it("skips POST while backoff is active, then retries unacked seqs", async () => {
    vi.useFakeTimers({
      now: 0,
      toFake: ["setTimeout", "clearTimeout", "Date"],
    });
    vi.spyOn(Math, "random").mockReturnValue(0);
    writeOpen("u-backoff", {
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ord: 0 },
      },
    });
    h.bearerPostJson.mockResolvedValue({ ok: false, status: 503, body: {} });
    const rec = openRecord("u-backoff", {
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ord: 0 },
      },
    });
    await checkpointOpenRecord(rec);
    expect(journalCalls()).toHaveLength(1);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    await checkpointOpenRecord(rec);
    expect(journalCalls()).toHaveLength(1);
    vi.setSystemTime(10_000);
    await checkpointOpenRecord(rec);
    expect(journalCalls()).toHaveLength(2);
    expect(journalCalls()[1]).toMatchObject({
      replace: false,
      entries: [
        { seq: 0, entry: { kind: "run_started", payload: { id: "r1" } } },
      ],
    });
  });

  it("warns once for a journal outage, then debugs repeats", async () => {
    vi.useFakeTimers({
      now: 0,
      toFake: ["setTimeout", "clearTimeout", "Date"],
    });
    vi.spyOn(Math, "random").mockReturnValue(0);
    writeOpen("u-warn", {
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ord: 0 },
      },
    });
    h.bearerPostJson.mockResolvedValue({ ok: false, status: 503, body: {} });
    const rec = openRecord("u-warn", {
      journal: {
        "0": { kind: "run_started", payload: { id: "r1" }, ord: 0 },
      },
    });
    await checkpointOpenRecord(rec);
    vi.setSystemTime(10_000);
    await checkpointOpenRecord(rec);
    const fails = journalFailLogs();
    expect(fails.filter((e) => e.level === "warn")).toHaveLength(1);
    expect(fails.filter((e) => e.level === "debug")).toHaveLength(1);
    expect(fails[0]?.fields?.repeat).toBe(false);
    expect(fails[1]?.fields?.repeat).toBe(true);
  });

  it("does not re-POST unchanged stream segments", async () => {
    h.bearerPostJson.mockResolvedValue({ ok: true, status: 200, body: {} });
    const segs = {
      "captain:content": { text: "half", generation: 0 },
    };
    await checkpointOpenRecord(openRecord("u-seg", { stream_segments: segs }));
    expect(segmentCalls()).toHaveLength(1);
    h.bearerPostJson.mockClear();
    await checkpointOpenRecord(openRecord("u-seg", { stream_segments: segs }));
    expect(segmentCalls()).toHaveLength(0);
    await checkpointOpenRecord(
      openRecord("u-seg", {
        stream_segments: {
          "captain:content": { text: "half more", generation: 0 },
        },
      }),
    );
    expect(segmentCalls()).toHaveLength(1);
  });
});
