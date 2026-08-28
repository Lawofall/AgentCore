import type { DescribedError } from "@/lib/errors";
import {
  OUR_SERVICE_UNAVAILABLE_MESSAGE,
  SELECTED_MODEL_UNAVAILABLE_MESSAGE,
  StreamError,
  connectivityEscalationSuffix,
  describeError,
  errorActionForCode,
  formatAssistantErrorMessage,
  isClientSideLlmRejection,
  isConnectivityErrorCode,
  isOurServiceErrorCode,
  isRetriableStreamError,
  isUnstartedSendRefusal,
  resetSessionConnectivityFailures,
  resolveAssistantFailureFace,
  syntheticErrorForEmptyFailure,
  syntheticErrorForHardFailure,
  visibleMessageText,
} from "@/lib/errors";
import { formatLocalMoment } from "@/lib/recoveryMoment";
import { afterEach, describe, expect, it } from "vitest";

describe("visibleMessageText", () => {
  it("prefers non-empty content over error (partial deliverable)", () => {
    expect(
      visibleMessageText({
        content: "半成品答案",
        error: { message: "模型调用失败，请重试。" },
      }),
    ).toBe("半成品答案");
  });

  it("falls back to error.message when content is empty", () => {
    expect(
      visibleMessageText({
        content: "  ",
        error: {
          message: "上游限流，暂时无法继续本回合。请稍后再试。",
        },
      }),
    ).toBe("上游限流，暂时无法继续本回合。请稍后再试。");
  });

  it("falls back to runs.error.message when message.error is absent", () => {
    expect(
      visibleMessageText({
        content: "",
        runs: { error: { message: "本地引擎启动失败" } },
      }),
    ).toBe("本地引擎启动失败");
  });

  it("does not hide content that equals the error string", () => {
    const same = "模型调用失败，请重试。";
    expect(
      visibleMessageText({
        content: same,
        error: { message: same },
      }),
    ).toBe(same);
  });

  it("returns empty when neither content nor error is present", () => {
    expect(visibleMessageText({ content: "" })).toBe("");
    expect(visibleMessageText({})).toBe("");
  });
});

describe("syntheticErrorForEmptyFailure", () => {
  it("synthesizes a card for empty error-finished turns", () => {
    expect(syntheticErrorForEmptyFailure("error")).toEqual({
      code: "LLM_ERROR",
      message: "模型调用失败，请重试。",
    });
  });

  it("synthesizes a card for empty unproductive-finished turns", () => {
    expect(syntheticErrorForEmptyFailure("unproductive")).toEqual({
      code: "LLM_UNPRODUCTIVE",
      message: "工具连续无有效进展或参数无效，请重试。",
    });
  });

  it("keeps upstream rate-limit product copy when code is known", () => {
    expect(syntheticErrorForEmptyFailure("error", "LLM_RATE_LIMIT")).toEqual({
      code: "LLM_RATE_LIMIT",
      message: "上游限流，暂时无法继续本回合。请稍后再试。",
    });
  });

  it("synthesizes cancelled / interrupted empty faces (B5 空泡)", () => {
    expect(syntheticErrorForEmptyFailure("cancelled")).toEqual({
      code: "TURN_CANCELLED",
      message: "已停止",
    });
    expect(syntheticErrorForEmptyFailure("interrupted")).toEqual({
      code: "TURN_INTERRUPTED",
      message: "已中断。直接发送下一条即可重试。",
    });
  });

  it("auth code wins over cancelled finish (platform face align)", () => {
    expect(
      syntheticErrorForEmptyFailure("cancelled", "LLM_KEY_INVALID"),
    ).toEqual({
      code: "LLM_KEY_INVALID",
      message:
        "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
    });
  });

  it("flips default ON for degraded; paused empty stays silent", () => {
    expect(syntheticErrorForEmptyFailure("degraded")).toEqual({
      code: "LLM_EMPTY_RESPONSE",
      message: "模型返回空内容，请重试。",
    });
    expect(syntheticErrorForEmptyFailure("paused")).toBeNull();
  });

  it("returns null for non-failure finishes", () => {
    expect(syntheticErrorForEmptyFailure("end_turn")).toBeNull();
    expect(syntheticErrorForEmptyFailure("max_rounds")).toBeNull();
    expect(syntheticErrorForEmptyFailure(undefined)).toBeNull();
  });
});

describe("resolveAssistantFailureFace", () => {
  it("surfaces any structured error source on empty content", () => {
    expect(
      resolveAssistantFailureFace({
        content: "",
        usageError: {
          code: "LLM_INSUFFICIENT_BALANCE",
          message: "上游账户余额不足，请充值或更换 Key。",
        },
        finishReason: "error",
      }),
    ).toEqual({
      code: "LLM_INSUFFICIENT_BALANCE",
      message: "上游账户余额不足，请充值或更换 Key。",
    });
  });

  it("paused is always silent (card or not); structured error still surfaces", () => {
    expect(
      resolveAssistantFailureFace({
        content: "",
        finishReason: "paused",
        hasDedicatedPauseOrAskUi: true,
      }),
    ).toBeNull();
    expect(
      resolveAssistantFailureFace({
        content: "",
        finishReason: "paused",
        hasDedicatedPauseOrAskUi: false,
      }),
    ).toBeNull();
    expect(
      resolveAssistantFailureFace({
        content: "",
        finishReason: "paused",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      }),
    ).toEqual({
      code: "LLM_ERROR",
      message: "模型调用失败，请重试。",
    });
  });
});

describe("upstream 429 is a refusal, not a connectivity fault", () => {
  // 生产实测：BYOK 用户当日额度用尽，上游 429 + Retry-After 指向次日 UTC 00:00。
  // 同一把 key 的另一个模型当时正常出字——Base URL / Key 都是好的，红卡却追加
  // 「检查 Base URL / API Key 与网络」，把人引去改一份本来正确的配置。
  // 后端只给不含时刻的兜底句 + 结构化 `recovery_at`，时刻由红卡按本机时区补。
  const QUOTA_RESET_MESSAGE =
    "上游限流，本回合无法继续。你的服务商额度恢复前重试仍会失败。";
  const RECOVERY_AT = "2026-08-14T16:00:00Z";

  afterEach(() => {
    resetSessionConnectivityFailures();
  });

  it("never escalates to Base URL / API Key, however many turns are refused", () => {
    expect(isConnectivityErrorCode("LLM_RATE_LIMIT")).toBe(false);
    for (const messageId of ["m1", "m2", "m3"]) {
      expect(
        connectivityEscalationSuffix("LLM_RATE_LIMIT", messageId, {
          message: QUOTA_RESET_MESSAGE,
          upstreamStatus: 429,
          conversationId: "c1",
        }),
      ).toBeNull();
    }
    // Also with no context at all — the code alone must decide.
    expect(connectivityEscalationSuffix("LLM_RATE_LIMIT", "m4")).toBeNull();
  });

  it("does not consume the counter that real connectivity failures need", () => {
    const opts = { conversationId: "c1" };
    connectivityEscalationSuffix("LLM_RATE_LIMIT", "m1", opts);
    connectivityEscalationSuffix("LLM_RATE_LIMIT", "m2", opts);
    expect(connectivityEscalationSuffix("LLM_TIMEOUT", "m3", opts)).toBeNull();
    expect(connectivityEscalationSuffix("LLM_TIMEOUT", "m4", opts)).toContain(
      "设置 · 服务商",
    );
  });

  it("keeps the backend's quota-recovery sentence verbatim on every outlet", () => {
    expect(
      formatAssistantErrorMessage({
        code: "LLM_RATE_LIMIT",
        message: QUOTA_RESET_MESSAGE,
      }),
    ).toBe(QUOTA_RESET_MESSAGE);
    expect(
      resolveAssistantFailureFace({
        content: "",
        finishReason: "error",
        error: { code: "LLM_RATE_LIMIT", message: QUOTA_RESET_MESSAGE },
      })?.message,
    ).toBe(QUOTA_RESET_MESSAGE);

    const described = describeError(
      new StreamError("http", 429, {
        code: "LLM_RATE_LIMIT",
        serverMessage: QUOTA_RESET_MESSAGE,
        retryAfter: 57_600,
      }),
    );
    expect(described?.message).toBe(QUOTA_RESET_MESSAGE);
    // Waiting is the only fix — no「去服务商」CTA next to a working key.
    expect(described?.action).toBeNull();
    expect(described?.retriable).toBe(false);
  });

  it("names the recovery moment in the user's own timezone, no zone label", () => {
    const local = formatLocalMoment(RECOVERY_AT);
    expect(local).not.toBeNull();

    // 红卡（SSE 把时刻挂在 error.context 上）。
    const card = formatAssistantErrorMessage({
      code: "LLM_RATE_LIMIT",
      message: QUOTA_RESET_MESSAGE,
      context: { recovery_at: RECOVERY_AT },
    });
    expect(card).toBe(`${QUOTA_RESET_MESSAGE}额度将于 ${local} 恢复。`);
    expect(card).not.toContain("UTC");

    // 横幅 / toast（REST 把时刻挂在 error 上）——与红卡同一句。
    const described = describeError(
      new StreamError("http", 429, {
        code: "LLM_RATE_LIMIT",
        serverMessage: QUOTA_RESET_MESSAGE,
        retryAfter: 57_600,
        recoveryMoment: { recovery_at: RECOVERY_AT },
      }),
    );
    expect(described?.message).toBe(card);

    // 平台配额闸门给的是 reset_at：说重置，同样只多出时刻。
    const gate = describeError(
      new StreamError("http", 429, {
        code: "QUOTA_EXCEEDED",
        serverMessage: "已达每日 token 上限（2,000,000 / 2,000,000）。",
        recoveryMoment: { reset_at: RECOVERY_AT },
      }),
    );
    expect(gate?.message).toBe(
      `已达每日 token 上限（2,000,000 / 2,000,000）。额度将于 ${local} 重置。`,
    );
  });

  it("上游没给时刻就不提时刻——绝不自己编一个", () => {
    const card = formatAssistantErrorMessage({
      code: "LLM_RATE_LIMIT",
      message: QUOTA_RESET_MESSAGE,
      context: { recovery_at: null, credential_source: "user" },
    });
    expect(card).toBe(QUOTA_RESET_MESSAGE);
    expect(
      describeError(
        new StreamError("http", 429, {
          code: "LLM_RATE_LIMIT",
          serverMessage: QUOTA_RESET_MESSAGE,
          recoveryMoment: { recovery_at: null, reset_at: null },
        }),
      )?.message,
    ).toBe(QUOTA_RESET_MESSAGE);
  });

  it("still normalizes legacy English rate-limit journals", () => {
    expect(
      formatAssistantErrorMessage({
        code: "LLM_RATE_LIMIT",
        message: "Rate limited by upstream",
      }),
    ).toBe("上游限流，暂时无法继续本回合。请稍后再试。");
  });
});

describe("syntheticErrorForHardFailure", () => {
  it("synthesizes when finishReason=error even if body exists", () => {
    expect(syntheticErrorForHardFailure("error")).toEqual({
      code: "LLM_ERROR",
      message: "模型调用失败，请重试。",
    });
  });

  it("prefers runs.error message when present", () => {
    expect(
      syntheticErrorForHardFailure("error", {
        code: "LLM_KEY_INVALID",
        message:
          "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
      }),
    ).toEqual({
      code: "LLM_KEY_INVALID",
      message:
        "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
    });
  });

  it("returns null for soft finishes", () => {
    expect(syntheticErrorForHardFailure("degraded")).toBeNull();
    expect(syntheticErrorForHardFailure("max_rounds")).toBeNull();
    expect(syntheticErrorForHardFailure(undefined)).toBeNull();
  });
});

describe("error action by type", () => {
  it("auth / balance → 去服务商; connectivity → null (retry in bubble)", () => {
    expect(errorActionForCode("LLM_KEY_INVALID")?.label).toBe("去服务商");
    expect(
      errorActionForCode("LLM_KEY_INVALID", { credentialSource: "user" })
        ?.label,
    ).toBe("去服务商");
    expect(
      errorActionForCode("LLM_KEY_INVALID", { credentialSource: "platform" })
        ?.label,
    ).toBe("接入自己的 Key");
    expect(
      errorActionForCode("LLM_KEY_INVALID", {
        message: "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key",
      })?.label,
    ).toBe("接入自己的 Key");
    expect(errorActionForCode("LLM_INSUFFICIENT_BALANCE")?.label).toBe(
      "去服务商",
    );
    expect(errorActionForCode("LLM_TIMEOUT")).toBeNull();
    expect(errorActionForCode("INFERENCE_TOKEN_EXPIRED")).toBeNull();
    expect(errorActionForCode("ALWAYS_QUOTA_EXCEEDED")).toEqual({
      label: "去整理",
      href: "/files",
    });
    expect(isConnectivityErrorCode("LLM_TIMEOUT")).toBe(true);
    expect(isConnectivityErrorCode("LLM_KEY_INVALID")).toBe(false);
  });

  it("inference JWT expiry → retry, never 去服务商 (incl. legacy English copy)", () => {
    const coded = describeError(
      new StreamError("http", undefined, {
        code: "INFERENCE_TOKEN_EXPIRED",
        serverMessage: "本地与云端的推理凭证已失效或过期。请稍后再试",
      }),
    );
    expect(coded?.action).toBeNull();
    expect(coded?.retriable).toBe(true);

    const legacy = describeError(
      new StreamError("http", undefined, {
        code: "LLM_KEY_INVALID",
        serverMessage: "user Invalid or expired inference token",
      }),
    );
    expect(legacy?.action).toBeNull();
    expect(legacy?.retriable).toBe(true);
    expect(legacy?.message).toContain("推理凭证");
  });

  it("CLIENT_TOO_OLD / 426 → force-update copy, non-retriable", () => {
    const coded = describeError(
      new StreamError("http", 426, {
        code: "CLIENT_TOO_OLD",
        serverMessage: "client too old",
      }),
    );
    expect(coded?.message).toBe("桌面端版本过旧，请更新后再试");
    expect(coded?.retriable).toBe(false);

    const byStatus = describeError(new StreamError("http", 426));
    expect(byStatus?.message).toBe("桌面端版本过旧，请更新后再试");
    expect(byStatus?.retriable).toBe(false);
  });
});

describe("isClientSideLlmRejection", () => {
  it("treats 4xx (except 429) as client rejection", () => {
    expect(isClientSideLlmRejection({ upstreamStatus: 400 })).toBe(true);
    expect(isClientSideLlmRejection({ upstreamStatus: 422 })).toBe(true);
    expect(isClientSideLlmRejection({ upstreamStatus: 429 })).toBe(false);
    expect(isClientSideLlmRejection({ upstreamStatus: 502 })).toBe(false);
  });

  it("matches invalid_request copy in message text", () => {
    expect(
      isClientSideLlmRejection({
        message:
          "platform 请求参数或消息格式不被当前模型支持，请检查 messages、tools、tool_choice",
      }),
    ).toBe(true);
    expect(
      isClientSideLlmRejection({
        message: '{"error":{"code":"invalid_request"}}',
      }),
    ).toBe(true);
    expect(isClientSideLlmRejection({ message: "连接超时" })).toBe(false);
  });
});

describe("connectivityEscalationSuffix", () => {
  afterEach(() => {
    resetSessionConnectivityFailures();
  });

  it("stays quiet on the first failure, escalates from the second message", () => {
    expect(connectivityEscalationSuffix("LLM_TIMEOUT", "m1")).toBeNull();
    expect(connectivityEscalationSuffix("LLM_TIMEOUT", "m2")).toContain(
      "设置 · 服务商",
    );
    // Same message id must not re-count.
    expect(connectivityEscalationSuffix("LLM_TIMEOUT", "m2")).toContain(
      "设置 · 服务商",
    );
  });

  it("ignores non-connectivity codes", () => {
    expect(connectivityEscalationSuffix("LLM_KEY_INVALID", "m1")).toBeNull();
    expect(connectivityEscalationSuffix(undefined, "m1")).toBeNull();
  });

  it("counts per conversation — a fresh chat never opens with 多次连接失败", () => {
    expect(
      connectivityEscalationSuffix("LLM_TIMEOUT", "a1", {
        conversationId: "chat-a",
      }),
    ).toBeNull();
    expect(
      connectivityEscalationSuffix("LLM_TIMEOUT", "a2", {
        conversationId: "chat-a",
      }),
    ).toContain("设置 · 服务商");
    // Same renderer (an Electron window lives for days): the first failure in
    // another chat is still a first failure.
    expect(
      connectivityEscalationSuffix("LLM_TIMEOUT", "b1", {
        conversationId: "chat-b",
      }),
    ).toBeNull();
    expect(
      connectivityEscalationSuffix("LLM_TIMEOUT", "b2", {
        conversationId: "chat-b",
      }),
    ).toContain("设置 · 服务商");
  });

  it("never escalates LLM_EMPTY_RESPONSE or emptyDiagnosis", () => {
    expect(isConnectivityErrorCode("LLM_EMPTY_RESPONSE")).toBe(false);
    expect(connectivityEscalationSuffix("LLM_EMPTY_RESPONSE", "m1")).toBeNull();
    expect(connectivityEscalationSuffix("LLM_EMPTY_RESPONSE", "m2")).toBeNull();
    // Even if a connectivity code somehow coexists with emptyDiagnosis, skip.
    expect(
      connectivityEscalationSuffix("LLM_TIMEOUT", "m1", {
        emptyDiagnosis: "silent_empty",
      }),
    ).toBeNull();
    expect(
      connectivityEscalationSuffix("LLM_TIMEOUT", "m2", {
        emptyDiagnosis: "upstream_non_api",
      }),
    ).toBeNull();
  });

  it("does not escalate upstream 400 invalid_request into connectivity hint", () => {
    expect(
      connectivityEscalationSuffix("LLM_ERROR", "m1", {
        message: "platform 请求参数或消息格式不被当前模型支持",
        upstreamStatus: 400,
      }),
    ).toBeNull();
    expect(
      connectivityEscalationSuffix("LLM_ERROR", "m2", {
        message: "platform 请求参数或消息格式不被当前模型支持",
        upstreamStatus: 400,
      }),
    ).toBeNull();
  });
});

describe("our-cloud DATABASE_UNAVAILABLE face", () => {
  afterEach(() => {
    resetSessionConnectivityFailures();
  });

  it("is not connectivity and never escalates to Base URL / API Key", () => {
    expect(isOurServiceErrorCode("DATABASE_UNAVAILABLE")).toBe(true);
    expect(isConnectivityErrorCode("DATABASE_UNAVAILABLE")).toBe(false);
    expect(
      connectivityEscalationSuffix("DATABASE_UNAVAILABLE", "m1"),
    ).toBeNull();
    expect(
      connectivityEscalationSuffix("DATABASE_UNAVAILABLE", "m2"),
    ).toBeNull();
    // Must not pollute the session counter used by true connectivity codes.
    expect(connectivityEscalationSuffix("LLM_TIMEOUT", "m3")).toBeNull();
  });

  it("honest product face, no settings CTA, still retriable", () => {
    const face = resolveAssistantFailureFace({
      content: "",
      finishReason: "error",
      error: {
        code: "DATABASE_UNAVAILABLE",
        message: OUR_SERVICE_UNAVAILABLE_MESSAGE,
      },
    });
    expect(face).toEqual({
      code: "DATABASE_UNAVAILABLE",
      message: OUR_SERVICE_UNAVAILABLE_MESSAGE,
    });
    expect(face?.message).not.toContain("上游模型服务");
    expect(face?.message).not.toContain("Base URL");
    expect(errorActionForCode("DATABASE_UNAVAILABLE")).toBeNull();

    const described = describeError(
      new StreamError("http", 503, {
        code: "DATABASE_UNAVAILABLE",
        serverMessage: OUR_SERVICE_UNAVAILABLE_MESSAGE,
      }),
    );
    expect(described?.message).toBe(OUR_SERVICE_UNAVAILABLE_MESSAGE);
    expect(described?.retriable).toBe(true);
    expect(described?.action).toBeNull();
  });

  it("vendor 530 mislabelled INTERNAL_ERROR is the selected model, not AgentCore", () => {
    const face = resolveAssistantFailureFace({
      content: "",
      finishReason: "error",
      error: {
        code: "INTERNAL_ERROR",
        message: OUR_SERVICE_UNAVAILABLE_MESSAGE,
        context: { upstream_status: 530 },
      },
    });
    expect(face).toEqual({
      code: "LLM_ERROR",
      message: SELECTED_MODEL_UNAVAILABLE_MESSAGE,
    });
    expect(face?.message).not.toContain("AgentCore");

    const described = describeError(
      new StreamError("http", 530, {
        code: "INTERNAL_ERROR",
        serverMessage: OUR_SERVICE_UNAVAILABLE_MESSAGE,
      }),
    );
    expect(described?.message).toBe(SELECTED_MODEL_UNAVAILABLE_MESSAGE);
    expect(described?.message).not.toContain("AgentCore");
  });

  it("true upstream LLM_ERROR still escalates connectivity from the 2nd failure", () => {
    expect(isConnectivityErrorCode("LLM_ERROR")).toBe(true);
    expect(connectivityEscalationSuffix("LLM_ERROR", "u1")).toBeNull();
    expect(connectivityEscalationSuffix("LLM_ERROR", "u2")).toContain(
      "设置 · 服务商",
    );
  });
});

describe("operator relay diagnosis never reaches the user", () => {
  // 平台模式下用户没有自己的 key —— Sub2API 探针描述的是运营方账号。后端已改为只写
  // 日志；这里钉住老 journal 残留的 context 也拼不出「诊断：…」。
  const UPSTREAM_503 = "上游模型服务暂时不可用（503），请稍后再试";
  const legacyContext = {
    upstream_status: 503,
    credential_source: "platform",
    sub2api_diagnosis:
      "账号 eli***@gmail.com token 有效但被上游拒绝，可能被限流或暂停",
    sub2api_account: "eli***@gmail.com",
  } as NonNullable<DescribedError["context"]>;

  it("formatAssistantErrorMessage keeps the product sentence verbatim", () => {
    const text = formatAssistantErrorMessage({
      code: "LLM_UPSTREAM_ERROR",
      message: UPSTREAM_503,
      context: legacyContext,
    });
    expect(text).toBe(UPSTREAM_503);
    expect(text).not.toContain("诊断");
    expect(text).not.toContain("@gmail.com");
  });

  it("describeError does not append it to the banner / toast either", () => {
    const described = describeError(
      new StreamError("http", 502, {
        code: "LLM_UPSTREAM_ERROR",
        serverMessage: UPSTREAM_503,
      }),
    );
    expect(described?.message).toBe(UPSTREAM_503);
    expect(described?.message).not.toContain("诊断");
  });

  it("preview / export outlets show the same sentence", () => {
    expect(
      visibleMessageText({
        content: "",
        error: { message: UPSTREAM_503 },
      }),
    ).toBe(UPSTREAM_503);
  });
});

describe("isUnstartedSendRefusal", () => {
  it("matches preflight key / quota / rate-limit / platform billing", () => {
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 402, { code: "LLM_KEY_REQUIRED" }),
      ),
    ).toBe(true);
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 429, { code: "QUOTA_EXCEEDED" }),
      ),
    ).toBe(true);
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 429, { code: "RATE_LIMITED" }),
      ),
    ).toBe(true);
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 503, {
          code: "PLATFORM_BILLING_UNAVAILABLE",
        }),
      ),
    ).toBe(true);
    expect(isUnstartedSendRefusal(new StreamError("http", 402))).toBe(true);
    expect(isUnstartedSendRefusal(new StreamError("http", 429))).toBe(true);
  });

  it("does not match mid-turn / transport codes", () => {
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 401, { code: "LLM_KEY_INVALID" }),
      ),
    ).toBe(false);
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 502, { code: "LLM_TIMEOUT" }),
      ),
    ).toBe(false);
    expect(isUnstartedSendRefusal(new StreamError("network"))).toBe(false);
    expect(
      isUnstartedSendRefusal(
        new StreamError("http", 503, { code: "INTERNAL_ERROR" }),
      ),
    ).toBe(false);
  });

  it("CONTEXT_OVERFLOW is not connectivity, not retriable, and rolls back the send", () => {
    const err = new StreamError("http", 413, {
      code: "CONTEXT_OVERFLOW",
      serverMessage:
        "这条对话对当前模型太长了。请开新对话，或换一个更能装长对话的模型。",
    });
    expect(isConnectivityErrorCode("CONTEXT_OVERFLOW")).toBe(false);
    expect(isUnstartedSendRefusal(err)).toBe(true);
    expect(isRetriableStreamError(err)).toBe(false);
  });
});
