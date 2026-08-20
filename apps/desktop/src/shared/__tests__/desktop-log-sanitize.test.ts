import { describe, expect, it } from "vitest";
import {
  DESKTOP_LOG_EXCERPT_MAX_EVENTS,
  type SanitizedDesktopLogRecord,
  foldDesktopLogRecords,
  hoistDesktopLogEnvelope,
  isRelevantDesktopLogRecord,
  sanitizeDesktopLogLines,
  sanitizeDesktopLogRecord,
} from "../desktop-log-sanitize";

describe("sanitizeDesktopLogRecord", () => {
  it("keeps server_health / sse diagnostic fields", () => {
    expect(
      sanitizeDesktopLogRecord({
        timestamp: "2026-08-17T00:00:00.000Z",
        level: "warn",
        event: "server_health.offline",
        build: "prod",
        version: "1.2.3",
        fields: {
          source: "heartbeat",
          reason: "连不上 AgentCore 服务，请稍后重试。",
          last_ok_at: 1,
          from: "online",
          consecutive_failures: 3,
        },
      }),
    ).toEqual({
      timestamp: "2026-08-17T00:00:00.000Z",
      level: "warn",
      event: "server_health.offline",
      build: "prod",
      version: "1.2.3",
      source: "heartbeat",
      reason: "连不上 AgentCore 服务，请稍后重试。",
      last_ok_at: 1,
      from: "online",
      consecutive_failures: 3,
    });
  });

  it("drops conversation body, tokens, and file paths even if present", () => {
    expect(
      sanitizeDesktopLogRecord({
        event: "sse.idle_stall",
        fields: {
          conversation_id: "c1",
          content: "用户的整段提问",
          token: "sk-secret",
          authorization: "Bearer abc",
          path: "C:\\\\Users\\\\me\\\\secret.docx",
          filename: "secret.docx",
          message: "should not leave the machine",
        },
      }),
    ).toEqual({
      event: "sse.idle_stall",
      conversation_id: "c1",
    });
  });

  it("drops non-diagnostic events", () => {
    expect(
      sanitizeDesktopLogRecord({
        event: "conversation.slice_diag",
        fields: { action: "load_latest_window", conversation_id: "c1" },
      }),
    ).toBeNull();
  });

  it("keeps sse.event_dropped enum fields and still strips bodies", () => {
    expect(
      sanitizeDesktopLogRecord({
        timestamp: "2026-08-20T00:00:00.000Z",
        level: "warn",
        event: "sse.event_dropped",
        build: "dev",
        version: "0.9.6",
        fields: {
          conversation_id: "c1",
          event_type: "content_delta",
          turn_phase: "stopping",
          reason: "turn_phase_gate",
          content: "用户的整段提问",
        },
      }),
    ).toEqual({
      timestamp: "2026-08-20T00:00:00.000Z",
      level: "warn",
      event: "sse.event_dropped",
      build: "dev",
      version: "0.9.6",
      conversation_id: "c1",
      event_type: "content_delta",
      turn_phase: "stopping",
      reason: "turn_phase_gate",
    });
  });
});

describe("sanitizeDesktopLogLines", () => {
  it("parses JSONL, drops junk / other conversations, caps the tail", () => {
    const lines = [
      "not-json",
      JSON.stringify({
        event: "server_health.online",
        fields: { since_offline_ms: 12 },
      }),
      JSON.stringify({
        event: "sse.idle_stall",
        fields: { conversation_id: "other" },
      }),
      JSON.stringify({
        event: "sse.idle_stall",
        fields: { conversation_id: "mine", content: "秘密正文" },
      }),
    ];
    const out = sanitizeDesktopLogLines(`partial-prefix\n${lines.join("\n")}`, {
      conversationId: "mine",
    });
    expect(out.map((line) => JSON.parse(line))).toEqual([
      { event: "server_health.online", since_offline_ms: 12 },
      { event: "sse.idle_stall", conversation_id: "mine" },
    ]);
    expect(out.join("\n")).not.toContain("秘密正文");
  });

  it("keeps server_health lines that have no conversation_id when packing a chat", () => {
    expect(
      isRelevantDesktopLogRecord(
        { event: "server_health.offline", source: "heartbeat" },
        "mine",
      ),
    ).toBe(true);
    expect(
      isRelevantDesktopLogRecord(
        { event: "sse.idle_stall", conversation_id: "other" },
        "mine",
      ),
    ).toBe(false);
    expect(
      isRelevantDesktopLogRecord(
        { event: "sse.forced_transport_drop" },
        "mine",
      ),
    ).toBe(true);
  });

  it("does not drop an early server_health.offline under a flood of scoped retries", () => {
    const rows = [
      JSON.stringify({
        event: "server_health.offline",
        fields: { source: "heartbeat" },
      }),
      ...Array.from({ length: DESKTOP_LOG_EXCERPT_MAX_EVENTS + 20 }, (_, i) =>
        JSON.stringify({
          event: "conversation.rejoin_retry",
          fields: { conversation_id: "mine", attempt: i },
        }),
      ),
    ];
    const out = sanitizeDesktopLogLines(rows.join("\n"), {
      conversationId: "mine",
    });
    const events = out.map((line) => JSON.parse(line).event);
    expect(events).toContain("server_health.offline");
    expect(out.length).toBeLessThanOrEqual(DESKTOP_LOG_EXCERPT_MAX_EVENTS);
  });

  it("caps scoped noise but keeps the newest ambient health edges", () => {
    const rows = Array.from(
      { length: DESKTOP_LOG_EXCERPT_MAX_EVENTS + 5 },
      (_, i) =>
        JSON.stringify({
          event: "server_health.probe_failed",
          fields: { attempt: i },
        }),
    );
    const out = sanitizeDesktopLogLines(rows.join("\n"));
    expect(out).toHaveLength(DESKTOP_LOG_EXCERPT_MAX_EVENTS);
    expect(JSON.parse(out[0] ?? "{}").attempt).toBe(5);
  });

  it("folds a follow_open storm into one line and still keeps server_health", () => {
    const storm = DESKTOP_LOG_EXCERPT_MAX_EVENTS + 20;
    const first = new Date(Date.UTC(2026, 7, 20, 0, 0, 0, 0)).toISOString();
    const last = new Date(
      Date.UTC(2026, 7, 20, 0, 0, 0, storm - 1),
    ).toISOString();
    const rows = [
      JSON.stringify({
        timestamp: "2026-08-20T00:00:00.000Z",
        event: "server_health.offline",
        fields: { source: "heartbeat" },
      }),
      ...Array.from({ length: storm }, (_, i) =>
        JSON.stringify({
          timestamp: new Date(Date.UTC(2026, 7, 20, 0, 0, 0, i)).toISOString(),
          level: "info",
          event: "conversation.follow_open",
          build: "dev",
          version: "0.9.6",
          fields: { conversation_id: "mine" },
        }),
      ),
    ];
    const out = sanitizeDesktopLogLines(rows.join("\n"), {
      conversationId: "mine",
    });
    const parsed = out.map(
      (line) => JSON.parse(line) as Record<string, unknown>,
    );
    expect(parsed.map((row) => row.event)).toEqual([
      "server_health.offline",
      "conversation.follow_open",
    ]);
    expect(parsed[1]).toMatchObject({
      event: "conversation.follow_open",
      conversation_id: "mine",
      count: storm,
      first,
      last,
    });
    expect(parsed[1]).not.toHaveProperty("timestamp");
  });

  it("does not fold rows that differ only by reason", () => {
    const out = sanitizeDesktopLogLines(
      [
        JSON.stringify({
          event: "conversation.follow_closed",
          fields: { conversation_id: "mine", reason: "window_closed" },
        }),
        JSON.stringify({
          event: "conversation.follow_closed",
          fields: {
            conversation_id: "mine",
            reason: "local_stream_handoff",
          },
        }),
      ].join("\n"),
      { conversationId: "mine" },
    );
    expect(out.map((line) => JSON.parse(line).reason)).toEqual([
      "window_closed",
      "local_stream_handoff",
    ]);
  });

  it("rolls up a handoff cycle that alternates closed / open", () => {
    const cycles = 18;
    const rows: string[] = [];
    for (let i = 0; i < cycles; i++) {
      const timestamp = new Date(
        Date.UTC(2026, 7, 20, 10, 43, i),
      ).toISOString();
      rows.push(
        JSON.stringify({
          timestamp,
          level: "info",
          event: "conversation.follow_closed",
          build: "dev",
          version: "0.9.6",
          fields: { conversation_id: "mine", reason: "local_stream_handoff" },
        }),
        JSON.stringify({
          timestamp,
          level: "info",
          event: "conversation.follow_open",
          build: "dev",
          version: "0.9.6",
          fields: { conversation_id: "mine" },
        }),
      );
    }
    rows.push(
      JSON.stringify({
        timestamp: "2026-08-20T11:18:43.852Z",
        level: "warn",
        event: "sse.event_dropped",
        build: "dev",
        version: "0.9.6",
        fields: {
          conversation_id: "mine",
          reason: "turn_phase_gate",
          event_type: "content_delta",
          turn_phase: "stopping",
        },
      }),
    );

    const out = sanitizeDesktopLogLines(rows.join("\n"), {
      conversationId: "mine",
    });
    const parsed = out.map(
      (line) => JSON.parse(line) as Record<string, unknown>,
    );
    expect(parsed).toHaveLength(3);
    expect(parsed[0]).toMatchObject({
      event: "conversation.follow_closed",
      reason: "local_stream_handoff",
      count: cycles,
    });
    expect(parsed[1]).toMatchObject({
      event: "conversation.follow_open",
      count: cycles,
    });
    expect(parsed[2]).toMatchObject({
      event: "sse.event_dropped",
      level: "warn",
      reason: "turn_phase_gate",
      turn_phase: "stopping",
    });
  });
});

describe("foldDesktopLogRecords", () => {
  it("rolls non-adjacent duplicates up into their first occurrence", () => {
    const out = foldDesktopLogRecords([
      {
        timestamp: "t0",
        event: "conversation.follow_open",
        conversation_id: "mine",
      },
      {
        timestamp: "t1",
        event: "conversation.follow_closed",
        conversation_id: "mine",
        reason: "local_stream_handoff",
      },
      {
        timestamp: "t2",
        event: "conversation.follow_open",
        conversation_id: "mine",
      },
    ]);
    expect(out).toEqual([
      {
        event: "conversation.follow_open",
        conversation_id: "mine",
        count: 2,
        first: "t0",
        last: "t2",
      },
      {
        timestamp: "t1",
        event: "conversation.follow_closed",
        conversation_id: "mine",
        reason: "local_stream_handoff",
      },
    ]);
  });

  it("never rolls up warn / error anchors", () => {
    const rows: SanitizedDesktopLogRecord[] = [
      { timestamp: "t0", level: "warn", event: "sse.event_dropped" },
      { timestamp: "t1", level: "warn", event: "sse.event_dropped" },
    ];
    expect(foldDesktopLogRecords(rows)).toEqual(rows);
  });
});

describe("hoistDesktopLogEnvelope", () => {
  it("marks ambient rows so a hoisted conversation_id cannot claim them", () => {
    const { header, records } = hoistDesktopLogEnvelope([
      {
        timestamp: "t0",
        event: "conversation.follow_open",
        conversation_id: "c1",
      },
      { timestamp: "t1", event: "server_health.offline", source: "heartbeat" },
    ]);
    expect(header.conversation_id).toBe("c1");
    expect(records).toEqual([
      { timestamp: "t0", event: "conversation.follow_open" },
      {
        timestamp: "t1",
        event: "server_health.offline",
        source: "heartbeat",
        scope: "app",
      },
    ]);
  });

  it("leaves rows unmarked when there is no conversation to hoist", () => {
    const { header, records } = hoistDesktopLogEnvelope([
      { timestamp: "t0", event: "server_health.offline" },
    ]);
    expect(header.conversation_id).toBeUndefined();
    expect(records).toEqual([
      { timestamp: "t0", event: "server_health.offline" },
    ]);
  });

  it("lifts shared envelope fields and keeps warn level on the line", () => {
    const { header, records } = hoistDesktopLogEnvelope([
      {
        timestamp: "t0",
        level: "info",
        event: "conversation.follow_open",
        build: "dev",
        version: "0.9.6",
        conversation_id: "c1",
      },
      {
        timestamp: "t1",
        level: "warn",
        event: "sse.event_dropped",
        build: "dev",
        version: "0.9.6",
        conversation_id: "c1",
        event_type: "content_delta",
        turn_phase: "stopping",
        reason: "turn_phase_gate",
      },
    ]);
    expect(header).toEqual({
      build: "dev",
      version: "0.9.6",
      conversation_id: "c1",
      level: "info",
    });
    expect(records).toEqual([
      { timestamp: "t0", event: "conversation.follow_open" },
      {
        timestamp: "t1",
        level: "warn",
        event: "sse.event_dropped",
        event_type: "content_delta",
        turn_phase: "stopping",
        reason: "turn_phase_gate",
      },
    ]);
  });
});
