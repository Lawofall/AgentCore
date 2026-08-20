import { describe, expect, it } from "vitest";
import {
  SUPPORT_DIAGNOSTIC_PREVIEW_MAX,
  formatSupportDiagnosticText,
  sanitizeSupportDiagnosticPreview,
  supportDiagnosticExtrasFromError,
} from "./supportDiagnosticExtras";

describe("sanitizeSupportDiagnosticPreview", () => {
  it("flattens whitespace and caps length", () => {
    const long = `${"a".repeat(SUPPORT_DIAGNOSTIC_PREVIEW_MAX)}EXTRA`;
    const out = sanitizeSupportDiagnosticPreview(`  foo\n\tbar  \n${long}`);
    expect(out.startsWith("foo bar ")).toBe(true);
    expect(out.length).toBe(SUPPORT_DIAGNOSTIC_PREVIEW_MAX);
    expect(out.endsWith("…")).toBe(true);
  });

  it("redacts API keys, bearer tokens, JWTs, and drops credential JSON fields", () => {
    const jwt = `eyJhbGciOiJIUzI1NiJ9.${"a".repeat(12)}.${"b".repeat(12)}`;
    const raw = [
      "sk-live-abc123",
      "Bearer super-secret-token",
      jwt,
      '{"api_key":"plain-secret","message":"Invalid schema for function x"}',
      '{"credential_id":"cred-9","authorization":"tok"}',
    ].join(" ");
    const out = sanitizeSupportDiagnosticPreview(raw);
    expect(out).not.toContain("sk-live-abc123");
    expect(out).not.toContain("super-secret-token");
    expect(out).not.toContain(jwt);
    expect(out).not.toContain("plain-secret");
    expect(out).not.toContain("cred-9");
    expect(out).not.toContain("api_key");
    expect(out).not.toContain("credential_id");
    expect(out).toContain("Invalid schema for function x");
    expect(out).toContain("[redacted]");
  });

  it("is idempotent after a first cap", () => {
    const raw = "x".repeat(SUPPORT_DIAGNOSTIC_PREVIEW_MAX + 40);
    const once = sanitizeSupportDiagnosticPreview(raw);
    expect(sanitizeSupportDiagnosticPreview(once)).toBe(once);
  });
});

describe("supportDiagnosticExtrasFromError", () => {
  it("maps empty-response fields and sets stream", () => {
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

  it("copies LLM schema-reject extras and skips blanks / credentials", () => {
    const context = {
      vendor_code: "invalid_request_error",
      model: "deepseek-chat",
      profile: "platform-fast",
      tool_count: 42,
      upstream_status: 400,
      upstream_body_preview:
        '{\n  "error": {"message": "Invalid schema for function \'browser_navigate\'", "code": "invalid_function_parameters"},\n  "api_key": "sk-should-not-leak"\n}',
      empty_diagnosis: "  ",
      credential_source: "user",
      api_key: "sk-direct",
    };
    const extras = supportDiagnosticExtrasFromError({
      code: "LLM_ERROR",
      context,
    });
    expect(extras).toEqual({
      errorCode: "LLM_ERROR",
      vendorCode: "invalid_request_error",
      model: "deepseek-chat",
      profile: "platform-fast",
      toolCount: 42,
      upstreamStatus: 400,
      upstreamBodyPreview:
        '{ "error": {"message": "Invalid schema for function \'browser_navigate\'", "code": "invalid_function_parameters"}}',
    });
    expect(JSON.stringify(extras)).not.toContain("credential");
    expect(JSON.stringify(extras)).not.toContain("sk-direct");
  });

  it("keeps tool_count 0 and omits stream for unrelated errors", () => {
    expect(
      supportDiagnosticExtrasFromError({
        code: "LLM_ERROR",
        context: { tool_count: 0, body_kind: "json" },
      }),
    ).toEqual({
      errorCode: "LLM_ERROR",
      bodyKind: "json",
      toolCount: 0,
    });
  });
});

describe("formatSupportDiagnosticText", () => {
  it("appends LLM extras after ids when present", () => {
    expect(
      formatSupportDiagnosticText({
        conversationId: "conv-1",
        messageId: "asst-client-uuid",
        userMessageId: "user-persisted",
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
        "user_message_id: user-persisted",
        "message_id: asst-client-uuid",
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

  it("still returns empty when only extras are set", () => {
    expect(
      formatSupportDiagnosticText({
        errorCode: "LLM_ERROR",
        vendorCode: "invalid_request_error",
        upstreamStatus: 400,
      }),
    ).toBe("");
  });
});
