import { describe, expect, it } from "vitest";
import {
  FINISH_REASON_LABELS,
  FINISH_REASON_META,
  MODEL_CONFIG_PATH,
  StreamHttpError,
  degradedFinishChipLabel,
  describeStreamHttpError,
  emptyChatCopy,
  emptyFailureNotice,
  emptyFailureVisibleNotice,
  errorActionForCode,
  isEmptyResponseUserSurface,
  isPausedFrameGone,
  isUnstartedSendRefusal,
  isZeroOutputSendRefusalCode,
  resolveEmptyFailureNotice,
} from "../errors";
import { formatLocalMoment } from "../recoveryMoment";

describe("errorActionForCode", () => {
  it("routes LLM_KEY_REQUIRED to 去配置", () => {
    expect(errorActionForCode("LLM_KEY_REQUIRED")).toEqual({
      label: "去配置",
      href: MODEL_CONFIG_PATH,
    });
  });

  it("routes LLM_KEY_INVALID to 去配置 (BYOK) or 接入自己的 Key (platform)", () => {
    expect(errorActionForCode("LLM_KEY_INVALID")).toEqual({
      label: "去配置",
      href: MODEL_CONFIG_PATH,
    });
    expect(
      errorActionForCode("LLM_KEY_INVALID", { credentialSource: "platform" }),
    ).toEqual({
      label: "接入自己的 Key",
      href: MODEL_CONFIG_PATH,
    });
  });

  it("offers a BYOK secondary exit for QUOTA_EXCEEDED (F6), null otherwise", () => {
    expect(errorActionForCode("QUOTA_EXCEEDED")).toEqual({
      label: "接入自己的 Key",
      href: MODEL_CONFIG_PATH,
    });
    expect(errorActionForCode("SOME_UNKNOWN")).toBeNull();
    expect(errorActionForCode(undefined)).toBeNull();
  });

  // 钱包见底有两条出路：去厂商充值（后端原句带），或换一把 Key —— 后者正是配置页干的事。
  it("routes LLM_INSUFFICIENT_BALANCE to 去配置", () => {
    expect(errorActionForCode("LLM_INSUFFICIENT_BALANCE")).toEqual({
      label: "去配置",
      href: MODEL_CONFIG_PATH,
    });
  });
});

describe("describeStreamHttpError", () => {
  it("prefers the backend message for LLM_KEY_REQUIRED and offers 去配置", () => {
    const err = new StreamHttpError(
      402,
      "LLM_KEY_REQUIRED",
      "请先接入自己的 API Key，再发起对话。",
    );
    expect(describeStreamHttpError(err)).toEqual({
      message: "请先接入自己的 API Key，再发起对话。",
      action: { label: "去配置", href: MODEL_CONFIG_PATH },
    });
  });

  it("falls back to a config hint when the body has no message", () => {
    const err = new StreamHttpError(402, "LLM_KEY_REQUIRED");
    const d = describeStreamHttpError(err);
    expect(d.message).toContain("API Key");
    expect(d.action?.label).toBe("去配置");
  });

  it("surfaces a generic status message without action when code is unknown", () => {
    const err = new StreamHttpError(500, undefined, undefined);
    expect(describeStreamHttpError(err)).toEqual({
      message: "请求失败 (500)",
      action: null,
    });
  });

  // 429 拒绝：后端只给结构化时刻 + 不含时刻的兜底句，本机时区的那句由前端出。
  // 真触发：`upstream_rate_limit_error` 平台日额度墙 → QUOTA_EXCEEDED（SSE 开流前 JSON）。
  it("renders a refusal's recovery moment in the device's own zone", () => {
    const server =
      "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，或接入自己的 API Key 立即继续。";
    const err = new StreamHttpError(429, "QUOTA_EXCEEDED", server, {
      recovery_at: "2026-08-14T16:00:00Z",
      credential_source: "platform",
    });
    const d = describeStreamHttpError(err);
    expect(d.message).toBe(
      `${server}额度将于 ${formatLocalMoment("2026-08-14T16:00:00Z")} 恢复。`,
    );
    expect(d.message.startsWith(server)).toBe(true);
    expect(d.message).not.toContain("上游将于");
    expect(d.message).not.toContain("UTC");
    expect(d.action).toEqual({
      label: "接入自己的 Key",
      href: MODEL_CONFIG_PATH,
    });
  });

  it("relays the server's timeless fallback when no moment came with it", () => {
    const fallback =
      "上游限流，本回合无法继续。你的服务商额度恢复前重试仍会失败。";
    const err = new StreamHttpError(429, "LLM_RATE_LIMIT", fallback);
    expect(describeStreamHttpError(err).message).toBe(fallback);
  });

  // 冷 resume 真失效的两句话是后端单一源；错误条必须原样转述（哪一种失效用户看得到）。
  it("relays the two frame-gone 404 messages verbatim, no remedy button", () => {
    for (const msg of [
      "这张卡已超过保留期被清理（挂起最多保留 7 天），请重新提问",
      "该回合已被重新生成或删除，这张卡不再有效",
    ]) {
      expect(
        describeStreamHttpError(new StreamHttpError(404, "NOT_FOUND", msg)),
      ).toEqual({
        message: msg,
        action: null,
      });
    }
  });
});

describe("isPausedFrameGone", () => {
  it("owns 404 / 410 — 挂起帧不在了，不该把卡放回可点", () => {
    expect(
      isPausedFrameGone(new StreamHttpError(404, "NOT_FOUND", "已清理")),
    ).toBe(true);
    expect(isPausedFrameGone(new StreamHttpError(410))).toBe(true);
  });

  it("leaves transient refusals / drops alone (卡该恢复可编辑)", () => {
    expect(isPausedFrameGone(new StreamHttpError(409))).toBe(false);
    expect(isPausedFrameGone(new StreamHttpError(500))).toBe(false);
    expect(isPausedFrameGone(new Error("network"))).toBe(false);
    expect(isPausedFrameGone(undefined)).toBe(false);
  });
});

describe("isZeroOutputSendRefusalCode", () => {
  it("matches first-upstream capability / rate codes (Class B)", () => {
    expect(isZeroOutputSendRefusalCode("LLM_RATE_LIMIT")).toBe(true);
    expect(isZeroOutputSendRefusalCode("LLM_KEY_INVALID")).toBe(true);
    expect(isZeroOutputSendRefusalCode("LLM_INSUFFICIENT_BALANCE")).toBe(true);
  });

  it("does not match Class A unstarted codes or generic failures", () => {
    expect(isZeroOutputSendRefusalCode("LLM_KEY_REQUIRED")).toBe(false);
    expect(isZeroOutputSendRefusalCode("QUOTA_EXCEEDED")).toBe(false);
    expect(isZeroOutputSendRefusalCode("RATE_LIMITED")).toBe(false);
    expect(isZeroOutputSendRefusalCode("PLATFORM_BILLING_UNAVAILABLE")).toBe(
      false,
    );
    expect(isZeroOutputSendRefusalCode("INTERNAL_ERROR")).toBe(false);
    expect(isZeroOutputSendRefusalCode(undefined)).toBe(false);
  });
});

describe("isUnstartedSendRefusal", () => {
  it("matches preflight key / quota / rate-limit / platform billing", () => {
    expect(
      isUnstartedSendRefusal(new StreamHttpError(402, "LLM_KEY_REQUIRED")),
    ).toBe(true);
    expect(
      isUnstartedSendRefusal(new StreamHttpError(429, "QUOTA_EXCEEDED")),
    ).toBe(true);
    expect(
      isUnstartedSendRefusal(new StreamHttpError(429, "RATE_LIMITED")),
    ).toBe(true);
    expect(
      isUnstartedSendRefusal(
        new StreamHttpError(503, "PLATFORM_BILLING_UNAVAILABLE"),
      ),
    ).toBe(true);
    expect(isUnstartedSendRefusal(new StreamHttpError(402))).toBe(true);
  });

  it("does not match mid-turn key-invalid or generic 503", () => {
    expect(
      isUnstartedSendRefusal(new StreamHttpError(401, "LLM_KEY_INVALID")),
    ).toBe(false);
    expect(
      isUnstartedSendRefusal(new StreamHttpError(503, "INTERNAL_ERROR")),
    ).toBe(false);
  });
});

describe("emptyChatCopy", () => {
  it("returns the no-gate welcome copy (platform-paid, keyless included)", () => {
    const copy = emptyChatCopy();
    expect(copy.title).toBe("开始新对话");
    expect(copy.subtitle).toContain("Agent 团队");
    expect(copy.action).toBeNull();
  });
});

describe("emptyFailureNotice", () => {
  it("explains empty error / unproductive finishes", () => {
    expect(emptyFailureNotice("error")).toBe("模型调用失败，请重试。");
    expect(emptyFailureNotice("unproductive")).toBe(
      "工具连续无有效进展或参数无效，请重试。",
    );
  });

  it("stays silent for normal / other finishes", () => {
    expect(emptyFailureNotice("end_turn")).toBeNull();
    expect(emptyFailureNotice("paused")).toBeNull();
    expect(emptyFailureNotice(null)).toBeNull();
    expect(emptyFailureNotice(undefined)).toBeNull();
  });

  it("flips default ON for degraded empty finishes", () => {
    expect(emptyFailureNotice("degraded")).toBe("模型返回空内容，请重试。");
  });

  it("surfaces empty max_rounds as the chip sentence", () => {
    expect(emptyFailureNotice("max_rounds")).toBe("已达最大轮次 · 提前收尾");
  });
});

describe("emptyFailureVisibleNotice", () => {
  it("prefers structured error.message over the generic finish notice", () => {
    expect(
      emptyFailureVisibleNotice("error", "API Key 已吊销，请重新配置。"),
    ).toBe("API Key 已吊销，请重新配置。");
  });

  it("falls back to emptyFailureNotice when error message is blank", () => {
    expect(emptyFailureVisibleNotice("error", "  ")).toBe(
      "模型调用失败，请重试。",
    );
    expect(emptyFailureVisibleNotice("error", null)).toBe(
      "模型调用失败，请重试。",
    );
    expect(emptyFailureVisibleNotice("unproductive", undefined)).toBe(
      "工具连续无有效进展或参数无效，请重试。",
    );
  });

  it("still surfaces a specific error when finishReason alone would be silent", () => {
    expect(emptyFailureVisibleNotice(null, "上游超时，请稍后重试。")).toBe(
      "上游超时，请稍后重试。",
    );
  });
});

describe("resolveEmptyFailureNotice (ChatPage gate)", () => {
  it("shows structured error on empty cold-load failure", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "",
        finishReason: "error",
        errorMessage: "配额已用尽",
      }),
    ).toBe("配额已用尽");
  });

  it("uses generic notice when empty + failure finish + no error payload", () => {
    expect(
      resolveEmptyFailureNotice({
        content: null,
        finishReason: "error",
      }),
    ).toBe("模型调用失败，请重试。");
  });

  it("surfaces structured error on half-reply (body + error payload)", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "半成品答复",
        finishReason: "error",
        errorMessage: "后面又挂了",
      }),
    ).toBe("后面又挂了");
  });

  it("synthesizes red-card copy for body + error finish without payload", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "半成品答复",
        finishReason: "error",
      }),
    ).toBe("模型调用失败，请重试。");
  });

  it("keeps soft finishes with body silent (chip owns soft surface)", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "降级后的短答",
        finishReason: "degraded",
      }),
    ).toBeNull();
  });

  it("skips while streaming / live", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "",
        finishReason: "error",
        errorMessage: "不该在流式中出现",
        skip: true,
      }),
    ).toBeNull();
  });

  it("empty cancelled does not synthesize a failure notice", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "",
        finishReason: "cancelled",
      }),
    ).toBeNull();
  });

  it("empty max_rounds uses the chip sentence", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "",
        finishReason: "max_rounds",
      }),
    ).toBe("已达最大轮次 · 提前收尾");
  });

  it("empty paused stays silent (not a failure face)", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "",
        finishReason: "paused",
      }),
    ).toBeNull();
    expect(
      resolveEmptyFailureNotice({
        content: null,
        finishReason: "paused",
        hasDedicatedPauseOrAskUi: true,
      }),
    ).toBeNull();
  });

  it("still surfaces structured error on empty paused", () => {
    expect(
      resolveEmptyFailureNotice({
        content: "",
        finishReason: "paused",
        errorMessage: "配额已用尽",
      }),
    ).toBe("配额已用尽");
  });
});

describe("degradedFinishChipLabel", () => {
  it("maps known empty_diagnosis keys", () => {
    expect(degradedFinishChipLabel("silent_empty", undefined)).toBe(
      "模型返回空内容",
    );
    expect(degradedFinishChipLabel("upstream_non_api", undefined)).toBe(
      "上游返回了网页或登录页，请检查服务商地址与鉴权",
    );
    expect(degradedFinishChipLabel("oauth_expired", undefined)).toBe(
      "上游返回了网页或登录页，请检查服务商地址与鉴权",
    );
    expect(degradedFinishChipLabel("length_empty", undefined)).toBe(
      "输出长度截断 · 返回空内容",
    );
  });

  it("falls back to message suffix after ·", () => {
    expect(degradedFinishChipLabel(undefined, "降级 · 内容被过滤")).toBe(
      "内容被过滤",
    );
  });
});

describe("isEmptyResponseUserSurface", () => {
  it("detects LLM_EMPTY_RESPONSE / diagnosis / empty-response copy", () => {
    expect(isEmptyResponseUserSurface({ code: "LLM_EMPTY_RESPONSE" })).toBe(
      true,
    );
    expect(isEmptyResponseUserSurface({ emptyDiagnosis: "silent_empty" })).toBe(
      true,
    );
    expect(
      isEmptyResponseUserSurface({ message: "模型多次空响应后收尾" }),
    ).toBe(true);
    expect(isEmptyResponseUserSurface({ code: "LLM_TIMEOUT" })).toBe(false);
  });
});

describe("FINISH_REASON_META / LABELS", () => {
  it("chip meta omits hard error; footer labels keep 调用失败", () => {
    expect(FINISH_REASON_META.error).toBeUndefined();
    expect(FINISH_REASON_META.degraded.label).toBe("空响应收尾");
    expect(FINISH_REASON_LABELS.error).toBe("调用失败");
  });
});
