import type { SSEEvent } from "@agentcore/contract-types";
import { describe, expect, it } from "vitest";
import {
  extractSupportIdsFromEvents,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportIdsFromEvents,
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

  it("returns empty string when nothing to copy", () => {
    expect(formatSupportDiagnosticText({})).toBe("");
  });

  it("does not emit extras-only packs (ids required)", () => {
    expect(
      formatSupportDiagnosticText({
        errorCode: "LLM_EMPTY_RESPONSE",
        emptyDiagnosis: "upstream_non_api",
        bodyKind: "html",
        baseUrl: "https://api.example.com",
        stream: true,
      }),
    ).toBe("");
  });

  it("appends error extras after ids when present", () => {
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

  it("omits stream line unless explicitly true", () => {
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        stream: false,
      }),
    ).toBe(
      [
        "阅读这段产品AI日志：",
        "conversation_id: conv-1",
        "uv run python scripts/log_timeline.py conv-1",
      ].join("\n"),
    );
  });

  it("lists user_message_id before assistant message_id", () => {
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

describe("extractSupportIdsFromEvents", () => {
  it("reads message_start + first run_plan + turn_saved ids", () => {
    const events = [
      {
        type: "message_start",
        timestamp: "t0",
        payload: {
          message_id: "m1",
          conversation_id: "c1",
          trace_id: "a".repeat(32),
        },
      },
      {
        type: "turn_saved",
        timestamp: "t0b",
        payload: { user_message_id: "u1" },
      },
      {
        type: "run_plan",
        timestamp: "t1",
        payload: { execution_id: "ex1" },
      },
    ] as SSEEvent[];
    expect(extractSupportIdsFromEvents(events)).toEqual({
      messageId: "m1",
      userMessageId: "u1",
      traceId: "a".repeat(32),
      executionId: "ex1",
    });
  });
});

describe("supportIdsFromEvents", () => {
  it("bubble and page-bar builders emit the same pack for one journal", () => {
    const events = [
      {
        type: "message_start",
        timestamp: "t0",
        payload: {
          message_id: "m1",
          conversation_id: "c1",
          trace_id: "a".repeat(32),
        },
      },
      {
        type: "run_plan",
        timestamp: "t1",
        payload: { execution_id: "ex1" },
      },
      {
        type: "error",
        timestamp: "t2",
        payload: {
          code: "LLM_RATE_LIMIT",
          message: "限流",
          context: { empty_diagnosis: undefined },
        },
      },
    ] as SSEEvent[];
    const bubble = supportIdsFromEvents("c1", events);
    const bar = supportIdsFromEvents("c1", events);
    expect(formatSupportDiagnosticText(bubble)).toBe(
      formatSupportDiagnosticText(bar),
    );
    expect(formatSupportDiagnosticText(bar)).toContain("message_id: m1");
    expect(formatSupportDiagnosticText(bar)).toContain("execution_id: ex1");
    expect(formatSupportDiagnosticText(bar)).toContain(
      "error_code: LLM_RATE_LIMIT",
    );
    expect(formatSupportDiagnosticText(bar)).not.toBe(
      formatSupportDiagnosticText({ conversationId: "c1" }),
    );
  });

  it("copies LLM schema-reject extras from the live error event", () => {
    const events = [
      {
        type: "message_start",
        timestamp: "t0",
        payload: {
          message_id: "m1",
          conversation_id: "c1",
          trace_id: "a".repeat(32),
        },
      },
      {
        type: "turn_saved",
        timestamp: "t1",
        payload: { user_message_id: "u1" },
      },
      {
        type: "error",
        timestamp: "t2",
        payload: {
          code: "LLM_ERROR",
          message: "模型调用失败，请重试。",
          context: {
            vendor_code: "invalid_request_error",
            model: "deepseek-chat",
            profile: "platform-fast",
            tool_count: 42,
            upstream_status: 400,
            upstream_body_preview:
              '{"error":{"message":"Invalid schema for function x","api_key":"sk-leak"}}',
          },
        },
      },
    ] as SSEEvent[];
    const pack = formatSupportDiagnosticText(
      supportIdsFromEvents("c1", events),
    );
    expect(pack).toContain("user_message_id: u1");
    expect(pack).toContain("error_code: LLM_ERROR");
    expect(pack).toContain("vendor_code: invalid_request_error");
    expect(pack).toContain("model: deepseek-chat");
    expect(pack).toContain("profile: platform-fast");
    expect(pack).toContain("tool_count: 42");
    expect(pack).toContain("upstream_status: 400");
    expect(pack).toContain("Invalid schema for function x");
    expect(pack).not.toContain("sk-leak");
    expect(pack).not.toContain("api_key");
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
