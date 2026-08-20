import { MODEL_CONFIG_PATH } from "@/lib/errors";
import { formatLocalMoment } from "@/lib/recoveryMoment";
import {
  FAILED_STRIP_TITLE,
  INTERRUPTED_STRIP_TITLE,
  PARTIAL_NOTICE,
  PAUSED_VERDICT,
  STOPPED_STRIP_TITLE,
  WAIT_UNKNOWN_HINT,
  isCeoContinuePause,
  resolveTurnOutcome,
  resolveTurnOutcomeFromJournal,
  teamFailureProgressBit,
  teamStripFace,
  toConformanceTurnVerdict,
  turnOutcomeShowsBubbleBanner,
  turnOutcomeShowsComposerHint,
  turnOwnsUserFacingOutlet,
  turnVerdictFromProjected,
  wireTurnResultFromPayload,
} from "@/lib/turnOutcome";
import type {
  MessageEndPayload,
  ProcessStep,
  SSEEvent,
} from "@agentcore/contract-types";
import type { ProjectedTurn } from "@agentcore/protocol-conformance/projectedTurn";
import { describe, expect, it } from "vitest";

function ev(type: SSEEvent["type"], payload: unknown): SSEEvent {
  return { type, timestamp: "t", payload } as SSEEvent;
}

const RATE_LIMIT_PARTIAL: SSEEvent[] = [
  ev("message_start", {
    message_id: "m1",
    conversation_id: "c1",
    trace_id: "a".repeat(32),
  }),
  ev("run_plan", { execution_id: "exec_rl" }),
  ev("run_failed", {
    run_id: "r1",
    agent_id: "w1",
    error: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
    product_landed: true,
    error_code: "LLM_RATE_LIMIT",
    retryable: true,
    retry_after: 4,
  }),
  ev("delivery_status", {
    execution_id: "exec_rl",
    state: "partial",
    summary: "已交付 3 个文件；1 项未完成",
  }),
  ev("error", {
    code: "LLM_RATE_LIMIT",
    message: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
    context: { retry_after: 4 },
  }),
  ev("message_end", { finish_reason: "error" }),
];

describe("resolveTurnOutcome · partial bits (no new heuristics)", () => {
  it("treats worker files + synthesis 429 as 部分完成, not a failure banner", () => {
    const out = resolveTurnOutcomeFromJournal({
      events: RATE_LIMIT_PARTIAL,
      content: "",
      finishReason: "interrupted",
    });
    expect(out.kind).toBe("partial");
    expect(out.surface).toBe("composer");
    expect(out.notice).toBe("上游限流，暂时无法继续本回合。请约 4 秒后再试。");
    expect(out.notice).not.toMatch(/已交付/);
    expect(out.notice).not.toMatch(/未能交付/);
    expect(out.recovery).toEqual({ kind: "wait", retryAfterSec: 4 });
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOutcomeShowsComposerHint(out)).toBe(true);
    expect(out.notice).not.toMatch(/已中断/);
    expect(out.notice).not.toMatch(/直接发送下一条/);
  });

  it("partial + retryable without retry_after still says 请稍后再试", () => {
    const out = resolveTurnOutcome({
      deliveryState: "partial",
      deliverySummary: "已交付 3 个文件；1 项未完成",
      retryable: true,
      errorCode: "LLM_RATE_LIMIT",
      errorMessage: "上游限流，暂时无法继续本回合。",
      finishReason: "error",
    });
    expect(out.kind).toBe("partial");
    expect(out.surface).toBe("composer");
    expect(out.recovery).toEqual({ kind: "wait_unknown" });
    expect(out.notice).toBe("上游限流，暂时无法继续本回合。");
    expect(out.notice).not.toMatch(/已交付/);
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOutcomeShowsComposerHint(out)).toBe(true);
  });

  it("product_landed alone is enough (delivery_status may be absent)", () => {
    const out = resolveTurnOutcome({
      events: [
        ev("run_failed", {
          run_id: "r1",
          agent_id: "w1",
          error: "boom",
          product_landed: true,
          retryable: false,
        }),
        ev("error", { code: "LLM_ERROR", message: "模型调用失败" }),
        ev("message_end", { finish_reason: "error" }),
      ],
    });
    expect(out.kind).toBe("partial");
    expect(out.recovery.kind).toBe("retry");
  });

  it("reads delegate tool meta.partial_failure", () => {
    const out = resolveTurnOutcome({
      events: [
        ev("tool_use_end", {
          tool_call_id: "dc1",
          tool_name: "delegate",
          status: "success",
          result: "团队完成（含 1 项失败）。",
          metadata: { partial_failure: true },
        }),
        ev("error", { code: "LLM_ERROR", message: "汇总失败" }),
        ev("message_end", { finish_reason: "error" }),
      ],
    });
    expect(out.kind).toBe("partial");
  });

  it("does not infer partial from artifacts-or-error heuristics", () => {
    const out = resolveTurnOutcome({
      events: [
        ev("error", { code: "LLM_ERROR", message: "挂了" }),
        ev("message_end", { finish_reason: "error" }),
      ],
      content: "半成品",
    });
    expect(out.kind).toBe("error");
    expect(out.notice).toBe("挂了");
  });
});

describe("resolveTurnOutcome · retryable recovery", () => {
  it("wait_unknown when retryable but retry_after is missing", () => {
    const out = resolveTurnOutcome({
      events: [
        ev("run_failed", {
          run_id: "r1",
          agent_id: "w1",
          error: "上游限流，暂时无法继续本回合。",
          error_code: "LLM_RATE_LIMIT",
          retryable: true,
        }),
        ev("error", {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。",
        }),
        ev("message_end", { finish_reason: "error" }),
      ],
      content: "",
    });
    expect(out.kind).toBe("error");
    expect(out.recovery).toEqual({ kind: "wait_unknown" });
    expect(out.notice).toContain(WAIT_UNKNOWN_HINT);
    expect(out.notice).not.toMatch(/请约/);
  });

  it("does not offer 重试 when waiting on a known retry_after", () => {
    const out = resolveTurnOutcome({
      retryable: true,
      retryAfter: 2,
      errorCode: "LLM_RATE_LIMIT",
      errorMessage: "上游限流，暂时无法继续本回合。请约 2 秒后再试。",
      finishReason: "error",
      content: "",
    });
    expect(out.recovery.kind).toBe("wait");
    expect(out.recovery).not.toEqual({ kind: "retry" });
    expect(out.recovery).not.toEqual({ kind: "send_next" });
  });

  it("terminal error offers retry, not wait", () => {
    const out = resolveTurnOutcome({
      errorCode: "LLM_ERROR",
      errorMessage: "模型调用失败，请重试。",
      finishReason: "error",
      retryable: false,
      content: "",
    });
    expect(out.kind).toBe("error");
    expect(out.recovery).toEqual({ kind: "retry" });
  });

  it("key-config codes stay 去配置 even when retryable is absent", () => {
    const out = resolveTurnOutcome({
      errorCode: "LLM_KEY_REQUIRED",
      errorMessage: "请先配置 API Key",
      finishReason: "error",
      content: "",
    });
    expect(out.recovery).toEqual({
      kind: "configure",
      label: "去配置",
      href: MODEL_CONFIG_PATH,
    });
  });
});

describe("resolveTurnOutcome · paused / cancelled / interrupted", () => {
  it("tolerates gate pause without treating it as failure", () => {
    const out = resolveTurnOutcome({
      finishReason: "paused",
      hasDedicatedPauseOrAskUi: true,
      content: "",
    });
    expect(out.kind).toBe("paused");
    expect(out.notice).toBeNull();
    expect(out.reason).toBeNull();
    expect(out.surface).toBe("none");
    expect(out.recovery).toEqual({ kind: "none" });
    expect(turnOwnsUserFacingOutlet(out)).toBe(false);
    expect(isCeoContinuePause(out)).toBe(false);
  });

  it("gate pause with error copy still defers to ResumeCard (outcome=null)", () => {
    const out = resolveTurnOutcome({
      finishReason: "paused",
      hasDedicatedPauseOrAskUi: true,
      errorCode: "LLM_RATE_LIMIT",
      errorMessage: "上游限流，暂时无法继续本回合。",
      content: "",
    });
    expect(out.kind).toBe("paused");
    expect(out.surface).toBe("none");
    expect(out.recovery).toEqual({ kind: "none" });
    expect(out.notice).toBeNull();
  });

  it("accepts wire result=paused", () => {
    expect(
      wireTurnResultFromPayload({
        finish_reason: "end_turn",
        result: "paused",
      } as MessageEndPayload & { result: string }),
    ).toBe("paused");
    const out = resolveTurnOutcome({
      events: [
        ev("message_end", { finish_reason: "end_turn", result: "paused" }),
      ],
    });
    expect(out.kind).toBe("paused");
    expect(out.surface).toBe("paused");
    expect(out.notice).toBe(PAUSED_VERDICT);
    expect(out.recovery).toEqual({ kind: "continue" });
  });

  it("reads message_end.outcome=paused as the continue face", () => {
    expect(
      wireTurnResultFromPayload({
        finish_reason: "paused",
        outcome: "paused",
      }),
    ).toBe("paused");
  });

  it("CEO rate-limit pause is 已暂停+继续, not partial/error dual recovery", () => {
    const out = resolveTurnOutcomeFromJournal({
      events: [
        ev("run_failed", {
          run_id: "r1",
          agent_id: "w1",
          error: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
          product_landed: true,
          error_code: "LLM_RATE_LIMIT",
          retryable: true,
          retry_after: 4,
        }),
        ev("delivery_status", {
          execution_id: "exec_rl",
          state: "partial",
          summary: "已交付 3 个文件；1 项未完成",
        }),
        ev("error", {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
          context: { retry_after: 4 },
        }),
        ev("message_end", { finish_reason: "paused", outcome: "paused" }),
      ],
      content: "已落盘。",
      finishReason: "paused",
    });
    expect(out.kind).toBe("paused");
    expect(out.surface).toBe("paused");
    expect(out.notice).toBe(PAUSED_VERDICT);
    expect(out.reason).toBe("上游限流，暂时无法继续本回合。请约 4 秒后再试。");
    expect(out.recovery).toEqual({ kind: "continue" });
    expect(out.recovery.kind).not.toBe("wait");
    expect(out.recovery.kind).not.toBe("retry");
    expect(out.notice).not.toMatch(/已交付/);
    expect(isCeoContinuePause(out)).toBe(true);
    expect(turnOwnsUserFacingOutlet(out)).toBe(true);
  });

  it("REST outcome=paused hydrates the continue face without a journal", () => {
    const out = resolveTurnOutcome({
      wireResult: "paused",
      finishReason: "paused",
      paused: true,
      errorMessage: "上游限流，暂时无法继续本回合。",
      content: "",
    });
    expect(isCeoContinuePause(out)).toBe(true);
    expect(out.recovery).toEqual({ kind: "continue" });
    expect(out.reason).toBe("上游限流，暂时无法继续本回合。");
  });

  it("empty cancelled is silent", () => {
    const out = resolveTurnOutcome({
      finishReason: "cancelled",
      content: "",
    });
    expect(out.kind).toBe("ok");
    expect(out.notice).toBeNull();
    expect(out.hideEmptyBubble).toBe(true);
  });

  it("cancelled with body keeps the draft and is not a failure card", () => {
    const out = resolveTurnOutcome({
      finishReason: "cancelled",
      content: "半成品",
    });
    expect(out.kind).toBe("ok");
    expect(out.notice).toBeNull();
    expect(out.hideEmptyBubble).toBe(false);
    expect(out.surface).toBe("none");
  });

  it("cancelled + 已停止 copy is still silent ok, not a warning banner", () => {
    const out = resolveTurnOutcome({
      finishReason: "cancelled",
      errorMessage: "已停止",
      content: "半成品",
      hasTeamGraph: true,
    });
    expect(out.kind).toBe("ok");
    expect(out.notice).toBeNull();
    expect(out.surface).toBe("none");
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
  });

  it("interrupted without error payload is send_next, not retry", () => {
    const out = resolveTurnOutcome({
      finishReason: "interrupted",
      content: "",
    });
    expect(out.kind).toBe("error");
    expect(out.recovery).toEqual({ kind: "send_next" });
    expect(out.notice).toMatch(/直接发送下一条/);
    expect(out.surface).toBe("error");
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(true);
    expect(out.hideEmptyBubble).toBe(false);
  });

  it("error payload wins over interrupted finishReason (no dual recovery)", () => {
    const out = resolveTurnOutcome({
      finishReason: "interrupted",
      errorCode: "LLM_RATE_LIMIT",
      errorMessage: "上游限流，暂时无法继续本回合。请约 2 秒后再试。",
      retryable: true,
      retryAfter: 2,
      content: "",
    });
    expect(out.kind).toBe("error");
    expect(out.recovery.kind).toBe("wait");
    expect(out.notice).not.toMatch(/直接发送下一条/);
  });
});

describe("teamFailureProgressBit", () => {
  it("uses 部分完成 when a failed worker already landed product", () => {
    expect(
      teamFailureProgressBit([
        { status: "failed", productLanded: true },
        { status: "completed" },
      ]),
    ).toBe(PARTIAL_NOTICE);
  });

  it("keeps N 失败 when nothing landed", () => {
    expect(
      teamFailureProgressBit([
        { status: "failed", productLanded: false },
        { status: "failed" },
      ]),
    ).toBe("2 失败");
  });
});

describe("resolveTurnOutcome · remaining terminals", () => {
  it("empty degraded recovers with retry on the banner, not a second hint", () => {
    const out = resolveTurnOutcome({
      finishReason: "degraded",
      content: "",
    });
    expect(out.kind).toBe("error");
    expect(out.notice).toBe("模型返回空内容，请重试。");
    expect(out.recovery).toEqual({ kind: "retry" });
    expect(out.surface).toBe("error");
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(true);
  });

  it("degraded with body stays chip-owned (no banner)", () => {
    const out = resolveTurnOutcome({
      finishReason: "degraded",
      content: "降级后的短答",
    });
    expect(out.notice).toBeNull();
    expect(out.surface).toBe("none");
    expect(out.recovery).toEqual({ kind: "none" });
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
  });

  it("empty unproductive is retry on the banner", () => {
    const out = resolveTurnOutcome({
      finishReason: "unproductive",
      content: "",
    });
    expect(out.notice).toBe("工具连续无有效进展或参数无效，请重试。");
    expect(out.recovery).toEqual({ kind: "retry" });
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(true);
  });

  it("unproductive with body does not invent a failed-tool hint", () => {
    const out = resolveTurnOutcome({
      finishReason: "unproductive",
      content: "已写完大半",
    });
    expect(out.notice).toBeNull();
    expect(out.surface).toBe("none");
    expect(out.recovery).toEqual({ kind: "none" });
    const v = toConformanceTurnVerdict({
      outcome: out,
      hasTeamStrip: false,
    });
    expect(v.hasTeamStrip).toBe(false);
    expect(v.supportPackHost).toBe("none");
    expect(v).not.toHaveProperty("surface");
    expect(v.failedToolHintNames).toEqual([]);
  });

  it("conformance envelope carries unproductive tool hint names", () => {
    const out = resolveTurnOutcome({
      finishReason: "unproductive",
      content: "已写完大半",
    });
    const v = toConformanceTurnVerdict({
      outcome: out,
      hasTeamStrip: false,
      failedToolHintNames: ["host_shell"],
    });
    expect(v.failedToolHintNames).toEqual(["host_shell"]);
    expect(v.hasTeamStrip).toBe(false);
    expect(v.supportPackHost).toBe("none");
  });

  it("turnVerdictFromProjected names failed tools on unproductive-with-body", () => {
    const process: ProcessStep[] = [
      { kind: "content", text: "已写完大半" },
      {
        kind: "tool",
        id: "tc1",
        tool_name: "host_shell",
        arguments: { command: "do_work" },
        result: "host_shell failed",
        status: "error",
      },
    ];
    const projected = {
      status: "completed",
      finishReason: "unproductive",
      outcome: "ok",
      error: null,
      content: "已写完大半",
      reasoning: "",
      captainContext: [],
      process,
      citations: [],
      evidenceLedger: [],
      citedIds: [],
      agents: [],
      runs: [],
      acts: [],
      progress: { completed: 0, total: 0 },
      interactions: [],
      cost: null,
      debate: null,
      debateRounds: [],
      debatePretrial: null,
      crossExamEnabled: false,
      debateOpening: null,
      teamSynthesisPreview: null,
      deliveryStatus: null,
      turnWarning: null,
      autoFolder: null,
      teamNotes: [],
      userInterjections: [],
    } as ProjectedTurn;
    const v = turnVerdictFromProjected([], projected);
    expect(v.failedToolHintNames).toEqual(["host_shell"]);
    expect(v.hasTeamStrip).toBe(false);
    expect(v.supportPackHost).toBe("none");
    expect(v).not.toHaveProperty("surface");
  });

  it("empty max_rounds is one notice, not a retry button", () => {
    const out = resolveTurnOutcome({
      finishReason: "max_rounds",
      content: "",
    });
    expect(out.kind).toBe("ok");
    expect(out.notice).toBe("已达最大轮次 · 提前收尾");
    expect(out.recovery).toEqual({ kind: "none" });
    expect(out.hideEmptyBubble).toBe(false);
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(true);
  });

  it("max_rounds with body stays silent for the banner (chip owns it)", () => {
    const out = resolveTurnOutcome({
      finishReason: "max_rounds",
      content: "提前收尾的正文",
    });
    expect(out.kind).toBe("ok");
    expect(out.notice).toBeNull();
    expect(out.surface).toBe("none");
  });

  it("QUOTA_EXCEEDED recovery is configure only, even if retryable", () => {
    const out = resolveTurnOutcome({
      errorCode: "QUOTA_EXCEEDED",
      errorMessage:
        "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，或接入自己的 API Key 立即继续。",
      finishReason: "error",
      retryable: true,
      content: "",
    });
    expect(out.recovery).toEqual({
      kind: "configure",
      label: "接入自己的 Key",
      href: MODEL_CONFIG_PATH,
    });
    expect(out.recovery.kind).not.toBe("retry");
    expect(out.notice).toContain("请等待上游额度恢复");
  });

  it("localizes QUOTA_EXCEEDED reset moment from the journal error context", () => {
    const server =
      "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，或接入自己的 API Key 立即继续。";
    const out = resolveTurnOutcomeFromJournal({
      events: [
        ev("error", {
          code: "QUOTA_EXCEEDED",
          message: server,
          context: {
            recovery_at: "2026-08-14T16:00:00Z",
            credential_source: "platform",
          },
        }),
        ev("message_end", { finish_reason: "error" }),
      ],
      content: "",
    });
    expect(out.recovery.kind).toBe("configure");
    expect(out.notice).toBe(
      `${server}额度将于 ${formatLocalMoment("2026-08-14T16:00:00Z")} 恢复。`,
    );
    expect(out.notice).not.toContain("上游将于");
    expect(out.notice).not.toContain("UTC");
  });
});

describe("resolveTurnOutcome · team strip owns the verdict", () => {
  it("error + team graph: strip surface, bubble banner off, notice kept for the strip", () => {
    const out = resolveTurnOutcome({
      finishReason: "error",
      errorMessage: "模型调用失败，请重试。",
      content: "",
      hasTeamGraph: true,
    });
    expect(out.kind).toBe("error");
    expect(out.surface).toBe("strip");
    expect(out.notice).toBe("模型调用失败，请重试。");
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOwnsUserFacingOutlet(out)).toBe(true);
    const v = toConformanceTurnVerdict({ outcome: out, hasTeamStrip: true });
    expect(v.hasTeamStrip).toBe(true);
    expect(v.supportPackHost).toBe("strip");
    expect(v).not.toHaveProperty("surface");
  });

  it("empty interrupt + team graph: composer owns the hint, strip is not the host", () => {
    const out = resolveTurnOutcome({
      finishReason: "interrupted",
      content: "",
      hasTeamGraph: true,
    });
    expect(out.surface).toBe("composer");
    expect(out.surface).not.toBe("strip");
    expect(out.recovery).toEqual({ kind: "send_next" });
    expect(out.notice).toMatch(/直接发送下一条/);
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOutcomeShowsComposerHint(out)).toBe(true);
    const v = toConformanceTurnVerdict({ outcome: out, hasTeamStrip: true });
    expect(v.hasTeamStrip).toBe(true);
    expect(v.supportPackHost).toBe("composer");
  });

  it("empty interrupt infers team graph from run_plan when the flag is omitted", () => {
    const out = resolveTurnOutcomeFromJournal({
      events: [
        ev("run_plan", { execution_id: "exec_int" }),
        ev("run_started", { run_id: "r1", agent_id: "w1" }),
        ev("message_end", { finish_reason: "interrupted" }),
      ],
      content: "",
      finishReason: "interrupted",
    });
    expect(out.surface).toBe("composer");
    expect(turnOutcomeShowsComposerHint(out)).toBe(true);
  });

  it("partial + team graph: strip owns 部分完成, not a second failure banner", () => {
    const out = resolveTurnOutcome({
      deliveryState: "partial",
      deliverySummary: "已交付 3 个文件；1 项未完成",
      finishReason: "error",
      hasTeamGraph: true,
    });
    expect(out.kind).toBe("partial");
    expect(out.surface).toBe("strip");
    expect(out.notice).toBeNull();
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOutcomeShowsComposerHint(out)).toBe(false);
  });

  it("partial + rate-limit + team graph: composer owns why, not strip sentences", () => {
    const out = resolveTurnOutcomeFromJournal({
      events: RATE_LIMIT_PARTIAL,
      content: "",
      finishReason: "interrupted",
      hasTeamGraph: true,
    });
    expect(out.kind).toBe("partial");
    expect(out.surface).toBe("composer");
    expect(out.surface).not.toBe("strip");
    expect(out.notice).toBe("上游限流，暂时无法继续本回合。请约 4 秒后再试。");
    expect(out.notice).not.toMatch(/已交付/);
    expect(out.notice).not.toMatch(/未能交付/);
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOutcomeShowsComposerHint(out)).toBe(true);
  });

  it("partial + rate-limit without team graph is not a bubble red card", () => {
    const out = resolveTurnOutcome({
      deliveryState: "partial",
      deliverySummary: "已交付 3 个文件；1 项未完成",
      retryable: true,
      retryAfter: 4,
      errorCode: "LLM_RATE_LIMIT",
      errorMessage: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
      finishReason: "error",
      hasTeamGraph: false,
    });
    expect(out.kind).toBe("partial");
    expect(out.surface).toBe("composer");
    expect(out.surface).not.toBe("strip");
    expect(out.notice).toBe("上游限流，暂时无法继续本回合。请约 4 秒后再试。");
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
    expect(turnOutcomeShowsComposerHint(out)).toBe(true);
  });

  it("paused continue is unchanged when a team graph is present", () => {
    const out = resolveTurnOutcome({
      wireResult: "paused",
      finishReason: "paused",
      errorMessage: "上游限流，暂时无法继续本回合。",
      hasTeamGraph: true,
      content: "",
    });
    expect(out.kind).toBe("paused");
    expect(out.surface).toBe("paused");
    expect(out.recovery).toEqual({ kind: "continue" });
    expect(isCeoContinuePause(out)).toBe(true);
    expect(turnOutcomeShowsBubbleBanner(out)).toBe(false);
  });
});

describe("teamStripFace", () => {
  it("does not paint 失败 from fold status when the arbiter says partial", () => {
    const face = teamStripFace("failed", {
      kind: "partial",
      recovery: { kind: "retry" },
    });
    expect(face.title).toBe(PARTIAL_NOTICE);
    expect(face.mark).toBe("ok");
  });

  it("interrupted is 已中断, not 已停止, even if fold status is cancelled", () => {
    const face = teamStripFace("cancelled", {
      kind: "error",
      recovery: { kind: "send_next" },
    });
    expect(face.title).toBe(INTERRUPTED_STRIP_TITLE);
    expect(face.title).not.toBe(STOPPED_STRIP_TITLE);
  });

  it("user-stop stays 已停止 when the arbiter is silent ok", () => {
    const face = teamStripFace("cancelled", {
      kind: "ok",
      recovery: { kind: "none" },
    });
    expect(face.title).toBe(STOPPED_STRIP_TITLE);
  });

  it("hard error is 失败", () => {
    const face = teamStripFace("failed", {
      kind: "error",
      recovery: { kind: "retry" },
    });
    expect(face.title).toBe(FAILED_STRIP_TITLE);
    expect(face.mark).toBe("err");
  });

  it("running wins over a stale outcome", () => {
    const face = teamStripFace("running", {
      kind: "error",
      recovery: { kind: "retry" },
    });
    expect(face.mark).toBe("run");
    expect(face.phase).toBe(true);
  });
});
