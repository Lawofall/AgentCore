import { MAX_RETRY_AFTER } from "@agentcore/contract-types";
import { turnVerdictHostContradiction } from "@agentcore/protocol-conformance/turnVerdict";
import { describe, expect, it } from "vitest";
import {
  LLM_RATE_LIMIT_MESSAGE,
  LLM_RATE_LIMIT_WHY,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
} from "../errors";
import {
  PARTIAL_STATUS_LABEL,
  PAUSED_CONTINUE_LABEL,
  PAUSED_STATUS_LABEL,
  type TurnOutcomeInput,
  arbitrateTurnOutcome,
  attestedWaitHint,
  isAttestedPauseContinue,
  toConformanceTurnVerdict,
} from "../turnOutcome";

function measuredCase(
  overrides: Partial<TurnOutcomeInput> = {},
): TurnOutcomeInput {
  return {
    content: "",
    finishReason: "interrupted",
    messageError: {
      code: "LLM_RATE_LIMIT",
      message: LLM_RATE_LIMIT_MESSAGE,
      context: {},
    },
    executionStatus: "failed",
    runs: [
      {
        id: "r1",
        status: "failed",
        error: LLM_RATE_LIMIT_MESSAGE,
        errorCode: "LLM_RATE_LIMIT",
        retryable: true,
        retryAfter: null,
        productLanded: true,
      },
    ],
    deliveryState: "partial",
    deliverySummary: "已交付 3 个文件；1 项未完成",
    conversationError: LLM_RATE_LIMIT_MESSAGE,
    ...overrides,
  };
}

describe("arbitrateTurnOutcome", () => {
  it("attested paused rate-limit: continue recovery, why-only reason, no banner", () => {
    const o = arbitrateTurnOutcome(measuredCase({ attestedKind: "paused" }));
    expect(o.kind).toBe("paused");
    expect(o.recovery.kind).toBe("continue");
    expect(o.recovery.label).toBe(PAUSED_CONTINUE_LABEL);
    expect(o.recovery.retryAfterSec ?? null).toBeNull();
    expect(isAttestedPauseContinue(o)).toBe(true);
    expect(o.recovery.kind).not.toBe("wait_then_retry");
    expect(o.recovery.kind).not.toBe("send_next");
    expect(o.showComposerHint).toBe(false);
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showSessionBanner).toBe(false);
    expect(o.showFooter).toBe(false);
    expect(o.showStripFailure).toBe(false);
    expect(o.showStripStopped).toBe(false);
    expect(o.supportPackHost).toBe("none");
    expect(o.message).toBe(LLM_RATE_LIMIT_WHY);
    expect(o.message).not.toMatch(/请约|稍后再试/);
    expect(o.message).not.toBe(TURN_INTERRUPTED_EMPTY_MESSAGE);
    expect(o.face?.code).toBe("LLM_RATE_LIMIT");
  });

  it("paused with attested Retry-After keeps why-only reason and gates continue", () => {
    const o = arbitrateTurnOutcome(
      measuredCase({
        attestedKind: "paused",
        messageError: {
          code: "LLM_RATE_LIMIT",
          message: `${LLM_RATE_LIMIT_WHY}请约 4 秒后再试。`,
          context: { retry_after: 4 },
        },
      }),
    );
    expect(o.kind).toBe("paused");
    expect(o.recovery.kind).toBe("continue");
    expect(o.recovery.retryAfterSec).toBe(4);
    expect(o.message).toBe(LLM_RATE_LIMIT_WHY);
    expect(o.message).not.toMatch(/请约|稍后再试/);
    expect(attestedWaitHint(4)).toBe("约 4 秒后可继续");
    expect(o.showBubbleBanner).toBe(false);
  });

  it("does not light interrupted send-next beside attested paused", () => {
    const o = arbitrateTurnOutcome(measuredCase({ attestedKind: "paused" }));
    expect(o.showBubbleBanner && o.showComposerHint).toBe(false);
    expect(o.recovery.kind).toBe("continue");
  });

  it("attested paused without retry_after keeps rate-limit as pause reason", () => {
    const o = arbitrateTurnOutcome(
      measuredCase({
        attestedKind: "paused",
        messageError: {
          code: "LLM_RATE_LIMIT",
          message: LLM_RATE_LIMIT_MESSAGE,
          context: {},
        },
        conversationError: LLM_RATE_LIMIT_MESSAGE,
        runs: [
          {
            id: "r1",
            status: "failed",
            error: LLM_RATE_LIMIT_MESSAGE,
            errorCode: "LLM_RATE_LIMIT",
            retryable: true,
            retryAfter: null,
            productLanded: true,
          },
        ],
      }),
    );
    expect(o.kind).toBe("paused");
    expect(o.recovery.kind).toBe("continue");
    expect(o.recovery.retryAfterSec ?? null).toBeNull();
    expect(o.message).toBe(LLM_RATE_LIMIT_WHY);
    expect(o.message).not.toMatch(/\d+\s*秒/);
    expect(o.message).not.toMatch(/请稍后再试/);
    expect(o.showComposerHint).toBe(false);
    expect(o.showBubbleBanner).toBe(false);
  });

  it("rate-limit without attested pause still waits (landed partial)", () => {
    const o = arbitrateTurnOutcome(measuredCase());
    expect(o.kind).toBe("partial");
    expect(o.recovery.kind).toBe("wait_then_retry");
    expect(o.recovery.kind).not.toBe("send_next");
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showComposerHint).toBe(true);
    expect(o.supportPackHost).toBe("composer");
    expect(o.showStripFailure).toBe(false);
    expect(o.message).toBe(LLM_RATE_LIMIT_WHY);
    expect(o.message).not.toMatch(/请约|稍后再试/);
    expect(o.message).not.toBe("已交付 3 个文件；1 项未完成");
  });

  it("partial folds attested wait into the composer hint, not the strip", () => {
    const o = arbitrateTurnOutcome(
      measuredCase({
        messageError: {
          code: "LLM_RATE_LIMIT",
          message: `${LLM_RATE_LIMIT_WHY}请约 4 秒后再试。`,
          context: { retry_after: 4 },
        },
      }),
    );
    expect(o.kind).toBe("partial");
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showComposerHint).toBe(true);
    expect(o.supportPackHost).toBe("composer");
    expect(o.recovery.kind).toBe("wait_then_retry");
    expect(o.message).toBe(`${LLM_RATE_LIMIT_WHY}约 4 秒后可继续。`);
    expect(o.recovery.retryAfterSec).toBe(4);
  });

  it("long attested retry_after suppresses wait_then_retry (matches 重试会失败 copy)", () => {
    const o = arbitrateTurnOutcome(
      measuredCase({
        messageError: {
          code: "LLM_RATE_LIMIT",
          message:
            "上游限流，本回合无法继续。你的服务商额度恢复前重试仍会失败。",
          context: { retry_after: MAX_RETRY_AFTER + 1 },
        },
        runs: [
          {
            id: "r1",
            status: "failed",
            errorCode: "LLM_RATE_LIMIT",
            retryable: true,
            retryAfter: MAX_RETRY_AFTER + 1,
            productLanded: true,
          },
        ],
      }),
    );
    expect(o.kind).toBe("partial");
    expect(o.recovery.kind).not.toBe("wait_then_retry");
    expect(o.showComposerHint).toBe(true);
    expect(o.showFooter).toBe(false);
  });

  it("retryable=false terminal recovery is not wait_then_retry", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "error",
      messageError: {
        code: "LLM_KEY_INVALID",
        message:
          "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
      },
      executionStatus: "failed",
      runs: [
        {
          id: "r1",
          status: "failed",
          errorCode: "LLM_KEY_INVALID",
          retryable: false,
          productLanded: false,
        },
      ],
    });
    expect(o.kind).toBe("error");
    expect(o.recovery.kind).toBe("configure");
    expect(o.recovery.kind).not.toBe("wait_then_retry");
    expect(o.showComposerHint).toBe(false);
    expect(o.showFooter).toBe(false);
    expect(o.showBubbleBanner).toBe(true);
    expect(o.supportPackHost).toBe("bubble");
  });

  it("interrupted-only recovery is send_next, not wait", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "interrupted",
    });
    expect(o.kind).toBe("error");
    expect(o.recovery.kind).toBe("send_next");
    expect(o.showComposerHint).toBe(true);
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showFooter).toBe(false);
    expect(o.supportPackHost).toBe("composer");
    expect(o.message).toBe(TURN_INTERRUPTED_EMPTY_MESSAGE);
    expect(o.face?.message).toBe(TURN_INTERRUPTED_EMPTY_MESSAGE);
  });

  it("pending decision suppresses the interrupted composer hint", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "interrupted",
      hasPendingDecision: true,
    });
    expect(o.kind).toBe("paused");
    expect(o.showComposerHint).toBe(false);
    expect(o.recovery.kind).toBe("resume");
  });

  it("attested paused without a face still offers continue", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      attestedKind: "paused",
    });
    expect(o.kind).toBe("paused");
    expect(o.recovery.kind).toBe("continue");
    expect(o.recovery.label).toBe(PAUSED_CONTINUE_LABEL);
    expect(o.showBubbleBanner).toBe(false);
    expect(PAUSED_STATUS_LABEL).toBe("已暂停");
  });

  it("attested partial wins over execution.failed paint", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      attestedKind: "partial",
      executionStatus: "failed",
      finishReason: "error",
      messageError: {
        code: "LLM_RATE_LIMIT",
        message: LLM_RATE_LIMIT_MESSAGE,
      },
      runs: [
        { id: "r1", status: "failed", retryable: true, productLanded: true },
      ],
    });
    expect(o.kind).toBe("partial");
    expect(o.kind).not.toBe("error");
  });

  it("attested error is not locally rewritten to partial", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      attestedKind: "error",
      executionStatus: "failed",
      deliveryState: "partial",
      runs: [{ id: "r1", status: "failed", productLanded: true }],
    });
    expect(o.kind).toBe("error");
    expect(o.kind).not.toBe("partial");
  });

  it("attested ok + delivery.partial paints 部分完成 even when the team graph completed", () => {
    const o = arbitrateTurnOutcome({
      content: "做到一半",
      attestedKind: "ok",
      executionStatus: "completed",
      deliveryState: "partial",
      hasTeamStrip: true,
    });
    expect(o.kind).toBe("partial");
  });

  it("completed graph + delivery.partial without attested still paints 部分完成", () => {
    const o = arbitrateTurnOutcome({
      content: "做到一半",
      executionStatus: "completed",
      deliveryState: "partial",
      hasTeamStrip: true,
    });
    expect(o.kind).toBe("partial");
  });

  it("empty cancelled hides the bubble; rate-limit on the same turn does not", () => {
    const cancelled = arbitrateTurnOutcome({
      content: "",
      finishReason: "cancelled",
    });
    expect(cancelled.kind).toBe("ok");
    expect(cancelled.hideEmptyBubble).toBe(true);
    expect(cancelled.showBubbleBanner).toBe(false);
    expect(cancelled.showComposerHint).toBe(false);
    expect(cancelled.showStripStopped).toBe(false);
    expect(cancelled.supportPackHost).toBe("none");

    const rateLimit = arbitrateTurnOutcome({
      content: "",
      finishReason: "cancelled",
      messageError: {
        code: "LLM_RATE_LIMIT",
        message: LLM_RATE_LIMIT_MESSAGE,
      },
    });
    expect(rateLimit.kind).toBe("error");
    expect(rateLimit.hideEmptyBubble).toBe(false);
    expect(rateLimit.showBubbleBanner).toBe(true);
    expect(rateLimit.face?.code).toBe("LLM_RATE_LIMIT");
    expect(rateLimit.showComposerHint).toBe(false);
    expect(rateLimit.showStripStopped).toBe(false);
    expect(rateLimit.supportPackHost).toBe("bubble");
  });

  it("exposes 部分完成 label for partial turns", () => {
    expect(PARTIAL_STATUS_LABEL).toBe("部分完成");
    const o = arbitrateTurnOutcome(measuredCase());
    expect(o.kind).toBe("partial");
  });

  it("gate pause (no attested outcome) keeps checkpoint resume, not continue", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "paused",
      hasDedicatedPauseOrAskUi: true,
    });
    expect(o.kind).toBe("paused");
    expect(o.recovery.kind).toBe("resume");
    expect(o.recovery.kind).not.toBe("continue");
    expect(isAttestedPauseContinue(o)).toBe(false);
    expect(o.showStripFailure).toBe(false);
    expect(o.supportPackHost).toBe("none");
  });
});

describe("arbitrateTurnOutcome · rest-of-states flag contract", () => {
  it("empty user-stop with a team strip is silent ok + 已停止, not error", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "cancelled",
      processLength: 1,
      hasTeamStrip: true,
      executionStatus: "cancelled",
    });
    expect(o.kind).toBe("ok");
    expect(o.hideEmptyBubble).toBe(false);
    expect(o.showStripStopped).toBe(true);
    expect(o.showStripFailure).toBe(false);
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showComposerHint).toBe(false);
    expect(o.showFooter).toBe(false);
    expect(o.showTurnWarning).toBe(false);
    expect(o.recovery.kind).toBe("none");
    expect(o.supportPackHost).toBe("none");
  });

  it("user-stop never paints a warning banner (session copy / attested error)", () => {
    const withSession = arbitrateTurnOutcome({
      content: "半成品",
      finishReason: "cancelled",
      hasTeamStrip: true,
      executionStatus: "cancelled",
      conversationError: "已停止",
      processLength: 2,
    });
    expect(withSession.kind).toBe("ok");
    expect(withSession.showStripStopped).toBe(true);
    expect(withSession.showBubbleBanner).toBe(false);
    expect(withSession.showSessionBanner).toBe(false);
    expect(withSession.showTurnWarning).toBe(false);

    const attestedError = arbitrateTurnOutcome({
      content: "半成品",
      finishReason: "cancelled",
      attestedKind: "error",
      hasTeamStrip: false,
      processLength: 1,
    });
    expect(attestedError.showBubbleBanner).toBe(false);
    expect(attestedError.showSessionBanner).toBe(false);
    expect(attestedError.showTurnWarning).toBe(false);

    const withPreflight = arbitrateTurnOutcome({
      content: "半成品",
      finishReason: "cancelled",
      hasTeamStrip: true,
      executionStatus: "cancelled",
      turnWarning: true,
      processLength: 2,
    });
    expect(withPreflight.showStripStopped).toBe(true);
    expect(withPreflight.showTurnWarning).toBe(false);
    expect(withPreflight.showBubbleBanner).toBe(false);
  });

  it("user-stop after resolved ask is ok, not paused — no bubble 已停止", () => {
    const o = arbitrateTurnOutcome({
      content: "好，按确认的方案开工",
      finishReason: "cancelled",
      hasTeamStrip: true,
      hasDedicatedPauseOrAskUi: true,
      executionStatus: "cancelled",
      processLength: 2,
    });
    expect(o.kind).toBe("ok");
    expect(o.kind).not.toBe("paused");
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showStripStopped).toBe(true);
    expect(o.showTurnWarning).toBe(false);
    expect(o.face?.code).toBe("TURN_CANCELLED");
  });

  it("attested paused leftover on the same bubble yields to user-stop", () => {
    const o = arbitrateTurnOutcome({
      content: "半成品",
      finishReason: "cancelled",
      attestedKind: "paused",
      hasTeamStrip: true,
      hasDedicatedPauseOrAskUi: true,
      executionStatus: "cancelled",
      processLength: 2,
    });
    expect(o.kind).toBe("ok");
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showStripStopped).toBe(true);
  });

  it("preflight turn_warning still paints when the turn is not a user-stop", () => {
    const o = arbitrateTurnOutcome({
      content: "正文",
      finishReason: "end_turn",
      turnWarning: true,
    });
    expect(o.kind).toBe("ok");
    expect(o.showTurnWarning).toBe(true);
    expect(o.showStripStopped).toBe(false);
    expect(o.showBubbleBanner).toBe(false);
  });

  it("rate-limit on cancelled status follows kind, never 已停止", () => {
    const o = arbitrateTurnOutcome(
      measuredCase({
        executionStatus: "cancelled",
        attestedKind: undefined,
        hasTeamStrip: true,
      }),
    );
    expect(o.kind).toBe("partial");
    expect(o.showStripStopped).toBe(false);
    expect(o.showStripFailure).toBe(false);
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showComposerHint).toBe(true);
    expect(o.supportPackHost).toBe("composer");
  });

  it("partial + rate-limit: composer owns why with or without a team strip", () => {
    for (const hasTeamStrip of [false, true]) {
      const o = arbitrateTurnOutcome(measuredCase({ hasTeamStrip }));
      expect(o.kind).toBe("partial");
      expect(o.recovery.kind).toBe("wait_then_retry");
      expect(o.recovery.kind).not.toBe("send_next");
      expect(o.showComposerHint).toBe(true);
      expect(o.showBubbleBanner).toBe(false);
      expect(o.showStripFailure).toBe(false);
      expect(o.supportPackHost).toBe("composer");
      expect(o.message).toBe(LLM_RATE_LIMIT_WHY);
      expect(o.message).not.toBe("已交付 3 个文件；1 项未完成");
    }
  });

  it("partial without rate-limit still hosts 排查包 on the strip", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      attestedKind: "partial",
      hasTeamStrip: true,
      deliveryState: "partial",
      deliverySummary: "已交付 1 个文件；1 项未完成",
      runs: [{ id: "r1", status: "failed", productLanded: true }],
    });
    expect(o.kind).toBe("partial");
    expect(o.showComposerHint).toBe(false);
    expect(o.supportPackHost).toBe("strip");
    expect(o.message).toBe("已交付 1 个文件；1 项未完成");
  });

  it("team strip owns the red card; bubble does not repeat it", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "error",
      messageError: {
        code: "LLM_TIMEOUT",
        message: "连接超时，请检查网络后重试。",
      },
      executionStatus: "failed",
      hasTeamStrip: true,
    });
    expect(o.kind).toBe("error");
    expect(o.showStripFailure).toBe(true);
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showSessionBanner).toBe(false);
    expect(o.showComposerHint).toBe(false);
    expect(o.supportPackHost).toBe("strip");
    expect(o.recovery.kind).toBe("wait_then_retry");
    expect(o.showFooter).toBe(false);
  });

  it("empty interrupt with a team strip still only lights the composer hint", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "interrupted",
      hasTeamStrip: true,
      processLength: 1,
    });
    expect(o.kind).toBe("error");
    expect(o.recovery.kind).toBe("send_next");
    expect(o.showComposerHint).toBe(true);
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showStripFailure).toBe(false);
    expect(o.showStripStopped).toBe(false);
    expect(o.supportPackHost).toBe("composer");
    expect(o.message).toBe(TURN_INTERRUPTED_EMPTY_MESSAGE);
  });

  it("QUOTA_EXCEEDED recovery is configure only; reset moment stays in the sentence", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "error",
      messageError: {
        code: "QUOTA_EXCEEDED",
        message: "本月平台额度已用完。",
        context: {
          reset_at: "2026-08-14T16:00:00Z",
          retry_after: 3600,
        },
      },
      executionStatus: "failed",
      runs: [
        {
          id: "r1",
          status: "failed",
          errorCode: "QUOTA_EXCEEDED",
          retryable: true,
          productLanded: false,
        },
      ],
    });
    expect(o.kind).toBe("error");
    expect(o.recovery.kind).toBe("configure");
    expect(o.recovery.label).toBe("接入自己的 Key");
    expect(o.recovery.kind).not.toBe("wait_then_retry");
    expect(o.showFooter).toBe(false);
    expect(o.showComposerHint).toBe(false);
    expect(o.message).toContain("本月平台额度已用完。");
    expect(o.message).toMatch(/额度将于 .+ 重置。/);
  });

  it("empty degraded matches retryable hard-fail: footer retry, no composer hint", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "degraded",
    });
    expect(o.kind).toBe("error");
    expect(o.recovery.kind).toBe("none");
    expect(o.showBubbleBanner).toBe(true);
    expect(o.showComposerHint).toBe(false);
    expect(o.showFooter).toBe(true);
    expect(o.supportPackHost).toBe("bubble");
  });

  it("empty unproductive is a failure card with footer retry", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "unproductive",
    });
    expect(o.kind).toBe("error");
    expect(o.showBubbleBanner).toBe(true);
    expect(o.showComposerHint).toBe(false);
    expect(o.showFooter).toBe(true);
    expect(o.supportPackHost).toBe("bubble");
  });

  it("max_rounds with body is a normal complete, not a failure face", () => {
    const o = arbitrateTurnOutcome({
      content: "写到一半",
      finishReason: "max_rounds",
    });
    expect(o.kind).toBe("ok");
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showComposerHint).toBe(false);
    expect(o.recovery.kind).toBe("none");
    expect(o.showFooter).toBe(true);
    expect(o.supportPackHost).toBe("none");
  });

  it("interrupted with body keeps the draft; no card, no hint", () => {
    const o = arbitrateTurnOutcome({
      content: "半成品",
      finishReason: "interrupted",
    });
    expect(o.kind).toBe("ok");
    expect(o.showBubbleBanner).toBe(false);
    expect(o.showComposerHint).toBe(false);
    expect(o.showFooter).toBe(true);
    expect(o.recovery.kind).toBe("none");
  });

  it("session banner lights only when no other verdict owns the copy", () => {
    const owned = arbitrateTurnOutcome({
      content: "",
      finishReason: "error",
      messageError: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      conversationError: "模型调用失败，请重试。",
    });
    expect(owned.showBubbleBanner).toBe(true);
    expect(owned.showSessionBanner).toBe(false);
    expect(owned.supportPackHost).toBe("bubble");

    const sessionOnly = arbitrateTurnOutcome({
      content: "已写出正文",
      conversationError: "网络中断，请重试。",
    });
    expect(sessionOnly.kind).toBe("error");
    expect(sessionOnly.showBubbleBanner).toBe(false);
    expect(sessionOnly.showSessionBanner).toBe(true);
    expect(sessionOnly.supportPackHost).toBe("session");
    expect(sessionOnly.message).toBe("网络中断，请重试。");
  });

  it("platform key-invalid still routes configure via credential_source", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "error",
      credentialSource: "platform",
      messageError: {
        code: "LLM_KEY_INVALID",
        message:
          "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
        context: { credential_source: "platform" },
      },
    });
    expect(o.recovery.kind).toBe("configure");
    expect(o.recovery.label).toBe("接入自己的 Key");
    expect(o.showFooter).toBe(false);
  });

  it("conformance envelope keeps hasTeamStrip + supportPackHost", () => {
    const o = arbitrateTurnOutcome({
      content: "",
      finishReason: "error",
      messageError: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      hasTeamStrip: true,
    });
    const v = toConformanceTurnVerdict({ outcome: o, hasTeamStrip: true });
    expect(v.hasTeamStrip).toBe(true);
    expect(v.supportPackHost).toBe("strip");
    expect(v).not.toHaveProperty("surface");
  });

  it("rejects contradictory host combos on the envelope", () => {
    expect(
      turnVerdictHostContradiction({
        hasTeamStrip: true,
        supportPackHost: "bubble",
      }),
    ).toMatch(/互斥/);
    expect(
      turnVerdictHostContradiction({
        hasTeamStrip: false,
        supportPackHost: "strip",
      }),
    ).toMatch(/互斥/);
    expect(
      turnVerdictHostContradiction({
        hasTeamStrip: true,
        supportPackHost: "composer",
      }),
    ).toBeNull();
  });
});
