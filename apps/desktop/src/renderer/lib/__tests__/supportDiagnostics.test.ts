import { afterEach, describe, expect, it, vi } from "vitest";
import {
  appendSanitizedDesktopLogExcerpt,
  buildSupportDiagnosticPack,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportDiagnosticExtrasFromError,
} from "../supportDiagnostics";

describe("formatSupportDiagnosticText", () => {
  it("joins present ids and prefers --trace log command", () => {
    const trace = "t".repeat(32);
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "msg-1",
        traceId: trace,
        executionId: "exec-1",
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "conversation_id: conv-1",
        "message_id: msg-1",
        `trace_id: ${trace}`,
        "execution_id: exec-1",
        `uv run python scripts/log_timeline.py --trace ${trace}`,
      ].join("\n"),
    );
  });

  it("falls back to conversation_id log command when no trace", () => {
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "msg-1",
        executionId: "exec-1",
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "conversation_id: conv-1",
        "message_id: msg-1",
        "execution_id: exec-1",
        "uv run python scripts/log_timeline.py conv-1",
      ].join("\n"),
    );
  });

  it("omits optional ids and log command when nothing to query", () => {
    expect(
      formatSupportDiagnosticText({
        messageId: "msg-1",
        traceId: null,
        executionId: "  ",
      }),
    ).toBe(["阅读这段产品AI日志：", "message_id: msg-1"].join("\n"));
  });

  it("returns empty string when nothing to copy", () => {
    expect(formatSupportDiagnosticText({})).toBe("");
  });

  it("still returns empty when only extras are set (needs at least one id)", () => {
    expect(
      formatSupportDiagnosticText({
        errorCode: "LLM_EMPTY_RESPONSE",
        emptyDiagnosis: "silent_empty",
        bodyKind: "html",
        baseUrl: "https://api.example.com",
        stream: true,
      }),
    ).toBe("");
  });

  it("appends extras after ids when present", () => {
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "msg-1",
        errorCode: "LLM_EMPTY_RESPONSE",
        emptyDiagnosis: "upstream_non_api",
        bodyKind: "html",
        baseUrl: "https://api.zdc.mom",
        stream: true,
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "conversation_id: conv-1",
        "message_id: msg-1",
        "error_code: LLM_EMPTY_RESPONSE",
        "empty_diagnosis: upstream_non_api",
        "body_kind: html",
        "base_url: https://api.zdc.mom",
        "stream: true",
        "uv run python scripts/log_timeline.py conv-1",
      ].join("\n"),
    );
  });

  it("omits stream when not true and skips blank extras", () => {
    expect(
      formatSupportDiagnosticText({
        messageId: "msg-1",
        errorCode: "LLM_ERROR",
        emptyDiagnosis: "  ",
        stream: false,
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "message_id: msg-1",
        "error_code: LLM_ERROR",
      ].join("\n"),
    );
  });

  it("lists user_message_id before assistant message_id for regenerate packs", () => {
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "asst-client-uuid",
        userMessageId: "user-persisted",
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "conversation_id: conv-1",
        "user_message_id: user-persisted",
        "message_id: asst-client-uuid",
        "uv run python scripts/log_timeline.py conv-1",
      ].join("\n"),
    );
  });

  it("appends LLM schema-reject extras after ids", () => {
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "asst-1",
        userMessageId: "user-1",
        errorCode: "LLM_ERROR",
        vendorCode: "invalid_request_error",
        model: "deepseek-chat",
        profile: "platform-fast",
        toolCount: 42,
        upstreamStatus: 400,
        upstreamBodyPreview:
          '{"error":{"message":"Invalid schema for function x"}}',
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "conversation_id: conv-1",
        "user_message_id: user-1",
        "message_id: asst-1",
        "error_code: LLM_ERROR",
        "vendor_code: invalid_request_error",
        "model: deepseek-chat",
        "profile: platform-fast",
        "tool_count: 42",
        "upstream_status: 400",
        'upstream_body_preview: {"error":{"message":"Invalid schema for function x"}}',
        "uv run python scripts/log_timeline.py conv-1",
      ].join("\n"),
    );
  });
});

describe("supportDiagnosticExtrasFromError", () => {
  it("maps error fields and sets stream for empty_diagnosis", () => {
    expect(
      supportDiagnosticExtrasFromError({
        code: "LLM_EMPTY_RESPONSE",
        context: {
          empty_diagnosis: "silent_empty",
          body_kind: "empty",
          base_url: "https://api.example.com/v1",
        },
      }),
    ).toEqual({
      errorCode: "LLM_EMPTY_RESPONSE",
      emptyDiagnosis: "silent_empty",
      bodyKind: "empty",
      baseUrl: "https://api.example.com/v1",
      stream: true,
    });
  });

  it("sets stream for LLM_EMPTY_RESPONSE without empty_diagnosis", () => {
    expect(
      supportDiagnosticExtrasFromError({ code: "LLM_EMPTY_RESPONSE" }),
    ).toEqual({
      errorCode: "LLM_EMPTY_RESPONSE",
      stream: true,
    });
  });

  it("omits stream for unrelated errors", () => {
    expect(
      supportDiagnosticExtrasFromError({
        code: "LLM_ERROR",
        context: { body_kind: "json" },
      }),
    ).toEqual({
      errorCode: "LLM_ERROR",
      bodyKind: "json",
    });
  });

  it("copies vendor/model/upstream extras for schema-reject failures", () => {
    expect(
      supportDiagnosticExtrasFromError({
        code: "LLM_ERROR",
        context: {
          vendor_code: "invalid_request_error",
          model: "deepseek-chat",
          profile: "platform-fast",
          tool_count: 42,
          upstream_status: 400,
          upstream_body_preview:
            '{"error":{"message":"Invalid schema for function x"}}',
        },
      }),
    ).toEqual({
      errorCode: "LLM_ERROR",
      vendorCode: "invalid_request_error",
      model: "deepseek-chat",
      profile: "platform-fast",
      toolCount: 42,
      upstreamStatus: 400,
      upstreamBodyPreview:
        '{"error":{"message":"Invalid schema for function x"}}',
    });
  });
});

describe("appendSanitizedDesktopLogExcerpt", () => {
  it("appends a desktop.jsonl section only when both pack and lines exist", () => {
    expect(appendSanitizedDesktopLogExcerpt("", ["{}"])).toBe("");
    expect(appendSanitizedDesktopLogExcerpt("pack", [])).toBe("pack");
    expect(
      appendSanitizedDesktopLogExcerpt("pack", ['{"event":"sse.idle_stall"}']),
    ).toBe(
      ["pack", "", "--- desktop.jsonl ---", '{"event":"sse.idle_stall"}'].join(
        "\n",
      ),
    );
  });

  it("folds a follow_open run and hoists envelope fields to the section head", () => {
    const lines = [
      ...Array.from({ length: 18 }, (_, i) =>
        JSON.stringify({
          timestamp: `2026-08-20T00:00:00.${String(i).padStart(3, "0")}Z`,
          level: "info",
          event: "conversation.follow_open",
          build: "dev",
          version: "0.9.6",
          conversation_id: "conv-1",
        }),
      ),
      ...Array.from({ length: 4 }, (_, i) =>
        JSON.stringify({
          timestamp: `2026-08-20T00:00:01.${String(i).padStart(3, "0")}Z`,
          level: "info",
          event: "conversation.follow_closed",
          build: "dev",
          version: "0.9.6",
          conversation_id: "conv-1",
          reason: "window_closed",
        }),
      ),
      JSON.stringify({
        timestamp: "2026-08-20T00:00:02.000Z",
        level: "warn",
        event: "sse.event_dropped",
        build: "dev",
        version: "0.9.6",
        conversation_id: "conv-1",
        event_type: "content_delta",
        turn_phase: "stopping",
        reason: "turn_phase_gate",
      }),
    ];
    const pack = appendSanitizedDesktopLogExcerpt("pack", lines);
    const section = pack.split("--- desktop.jsonl ---\n")[1] ?? "";
    const sectionLines = section.split("\n");
    expect(sectionLines.slice(0, 4)).toEqual([
      "build: dev",
      "version: 0.9.6",
      "conversation_id: conv-1",
      "level: info",
    ]);
    const jsonl = sectionLines.slice(4).map((line) => JSON.parse(line));
    expect(jsonl).toEqual([
      {
        event: "conversation.follow_open",
        count: 18,
        first: "2026-08-20T00:00:00.000Z",
        last: "2026-08-20T00:00:00.017Z",
      },
      {
        event: "conversation.follow_closed",
        reason: "window_closed",
        count: 4,
        first: "2026-08-20T00:00:01.000Z",
        last: "2026-08-20T00:00:01.003Z",
      },
      {
        timestamp: "2026-08-20T00:00:02.000Z",
        level: "warn",
        event: "sse.event_dropped",
        event_type: "content_delta",
        turn_phase: "stopping",
        reason: "turn_phase_gate",
      },
    ]);
  });
});

describe("buildSupportDiagnosticPack", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stays ids-only when logApi is missing", async () => {
    vi.stubGlobal("window", {});
    await expect(
      buildSupportDiagnosticPack({ conversationId: "conv-1", messageId: "m1" }),
    ).resolves.toBe(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "m1",
      }),
    );
  });

  it("keeps server_health.offline with no conversation_id in a conversation pack", async () => {
    vi.stubGlobal("window", {
      logApi: {
        write: () => {},
        readTail: async () => [
          JSON.stringify({
            event: "server_health.offline",
            source: "heartbeat",
          }),
          JSON.stringify({
            event: "sse.idle_stall",
            conversation_id: "other-chat",
          }),
          JSON.stringify({
            event: "sse.idle_stall",
            conversation_id: "conv-1",
          }),
        ],
      },
    });
    const pack = await buildSupportDiagnosticPack({
      conversationId: "conv-1",
      messageId: "m1",
    });
    expect(pack).toContain("server_health.offline");
    expect(pack).toContain("sse.idle_stall");
    expect(pack).toContain("conversation_id: conv-1");
    expect(pack).not.toContain("other-chat");
    const section = pack.split("--- desktop.jsonl ---\n")[1] ?? "";
    expect(section).toContain("conversation_id: conv-1");
    expect(section).not.toMatch(/"conversation_id"/);
    expect(section).not.toMatch(/"level":"info"/);
  });

  it("appends sanitized tail lines from logApi.readTail", async () => {
    vi.stubGlobal("window", {
      logApi: {
        write: () => {},
        readTail: async () => [
          JSON.stringify({
            event: "server_health.offline",
            source: "heartbeat",
          }),
          JSON.stringify({
            event: "sse.idle_stall",
            conversation_id: "conv-1",
          }),
        ],
      },
    });
    const pack = await buildSupportDiagnosticPack({
      conversationId: "conv-1",
      messageId: "m1",
    });
    expect(pack).toContain("conversation_id: conv-1");
    expect(pack).toContain("--- desktop.jsonl ---");
    expect(pack).toContain("server_health.offline");
    expect(pack).not.toContain("token");
  });
});

describe("precedingUserMessageId", () => {
  it("returns the nearest prior user bubble", () => {
    expect(
      precedingUserMessageId(
        [
          { id: "u1", role: "user" },
          { id: "a1", role: "assistant" },
          { id: "u2", role: "user" },
          { id: "a2", role: "assistant" },
        ],
        "a2",
      ),
    ).toBe("u2");
  });
});
