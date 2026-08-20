/**
 * Turn-level outcome arbiter (单一错误出口).
 *
 * Kind is `ok | partial | paused | error` — same enum the server persists on
 * `turn_metrics.status`. Judgment only aggregates batch-layer bits already on
 * the wire; it does not invent heuristics:
 *   - `delivery_status.state === "partial"`
 *   - `run_failed.product_landed`
 *   - delegate tool meta `partial_failure`
 *   - optional `message_end.result` / `turn_result` once the server emits it
 *
 * `paused` on the wire (`message_end.outcome=paused`) is the CEO rate-limit
 * continue face — distinct from gate pauses (`finish_reason=paused` and
 * `outcome=null`), which keep surface none so ResumeCard / PauseCard own them.
 *
 * `partial` + structured `LLM_RATE_LIMIT` hangs the why on `surface=composer`
 * (ChatPage input hint). Empty interrupt + team graph does the same — the
 * 「已中断，发下一条即可」sentence lives next to the input, not on the strip.
 * Team strip still paints 部分完成 战绩; it must not host the why or the
 * delivery summary. `kind=paused` stays on PausedContinueCard.
 */
import { errorActionForCode, resolveEmptyFailureNotice } from "@/lib/errors";
import { withLocalRecoveryMoment } from "@/lib/recoveryMoment";
import {
  collectFailedToolNames,
  shouldShowUnproductiveToolFailureHint,
} from "@/lib/unproductiveToolFailureHint";
import type {
  DeliveryStatusPayload,
  ErrorPayload,
  MessageEndPayload,
  RunFailedPayload,
  SSEEvent,
} from "@agentcore/contract-types";
import type {
  ProjectedTurn,
  TurnStatus,
} from "@agentcore/protocol-conformance/projectedTurn";
import {
  type ProjectedTurnVerdict,
  type TurnSupportPackHost,
  projectedHasDedicatedPauseUi,
  projectedHasTeamGraph,
} from "@agentcore/protocol-conformance/turnVerdict";

export type TurnOutcomeKind = "ok" | "partial" | "paused" | "error";

export type TurnRecovery =
  | { kind: "none" }
  | { kind: "wait"; retryAfterSec: number }
  | { kind: "wait_unknown" }
  | { kind: "retry" }
  | { kind: "configure"; label: string; href: string }
  | { kind: "send_next" }
  | { kind: "continue" };

export type TurnOutcomeSurface =
  | "none"
  | "error"
  | "paused"
  /** Team strip owns the verdict; bubble must not repeat the same failure sentence. */
  | "strip"
  /** Input-area light hint (empty interrupt / partial + rate-limit why). Not a banner / red card. */
  | "composer";

export type TurnOutcome = {
  kind: TurnOutcomeKind;
  /** User-facing primary sentence. null = no extra banner. */
  notice: string | null;
  /** Pause-face explanation (rate-limit copy). Not a second verdict. */
  reason: string | null;
  surface: TurnOutcomeSurface;
  recovery: TurnRecovery;
  errorCode?: string | null;
  retryable?: boolean | null;
  /** Empty user-stop: omit the bubble. Callers still AND with their empty-shell check. */
  hideEmptyBubble: boolean;
};

export const PARTIAL_NOTICE = "部分完成";
/** Structured `LLM_RATE_LIMIT` why — unique judgment sentence for composer hint. */
export const RATE_LIMIT_WHY = "上游限流，暂时无法继续本回合。";
export const PAUSED_VERDICT = "已暂停";
export const WAIT_UNKNOWN_HINT = "请稍后再试。";
export const FAILED_STRIP_TITLE = "失败";
export const STOPPED_STRIP_TITLE = "已停止";
export const INTERRUPTED_STRIP_TITLE = "已中断";

const WIRE_KINDS = new Set<TurnOutcomeKind>([
  "ok",
  "partial",
  "paused",
  "error",
]);

function asWireKind(value: unknown): TurnOutcomeKind | null {
  return typeof value === "string" && WIRE_KINDS.has(value as TurnOutcomeKind)
    ? (value as TurnOutcomeKind)
    : null;
}

function eventsHaveTeamGraph(events: readonly SSEEvent[]): boolean {
  return events.some((e) => e.type === "run_plan" || e.type === "run_started");
}

function resolveHasTeamGraph(input: TurnOutcomeInput): boolean {
  if (typeof input.hasTeamGraph === "boolean") return input.hasTeamGraph;
  return eventsHaveTeamGraph(input.events ?? []);
}

function supportPackHostFromSurface(
  surface: TurnOutcomeSurface,
): TurnSupportPackHost {
  if (surface === "error") return "bubble";
  if (surface === "composer") return "composer";
  if (surface === "strip") return "strip";
  return "none";
}

/** Server-authored result on `message_end` (`outcome`; older journals used result). */
export function wireTurnResultFromPayload(
  payload: MessageEndPayload | null | undefined,
): TurnOutcomeKind | null {
  if (!payload) return null;
  const extra = payload as MessageEndPayload & {
    result?: unknown;
    turn_result?: unknown;
  };
  return (
    asWireKind(extra.outcome) ??
    asWireKind(extra.result) ??
    asWireKind(extra.turn_result)
  );
}

function hasWaitStory(text: string): boolean {
  return /请约|稍后再试|稍候|恢复/.test(text);
}

function formatRetryAfterSec(sec: number): string {
  if (sec < 1) return "片刻";
  const rounded = Math.round(sec);
  return `${rounded} 秒`;
}

function withTransientWaitHint(
  notice: string,
  retryable: boolean | null,
  retryAfter: number | null,
): string {
  if (retryable !== true || hasWaitStory(notice)) return notice;
  const base = notice.replace(/。+$/, "");
  if (retryAfter != null && retryAfter > 0) {
    return `${base}。请约 ${formatRetryAfterSec(retryAfter)} 后再试。`;
  }
  return `${base}。${WAIT_UNKNOWN_HINT}`;
}

export function collectPartialBits(input: {
  deliveryState?: string | null;
  deliverySummary?: string | null;
  productLanded?: boolean;
  partialFailureMeta?: boolean;
  events?: readonly SSEEvent[];
  runs?: readonly { productLanded?: boolean | null }[];
}): { partial: boolean; summary: string | null } {
  let partial = input.deliveryState === "partial";
  let summary =
    input.deliveryState === "partial"
      ? (input.deliverySummary?.trim() ?? null)
      : null;
  if (input.productLanded) partial = true;
  if (input.partialFailureMeta) partial = true;
  if (input.runs?.some((r) => r.productLanded === true)) partial = true;
  for (const e of input.events ?? []) {
    if (e.type === "delivery_status") {
      const p = e.payload as DeliveryStatusPayload;
      if (p.state === "partial") {
        partial = true;
        if (!summary) summary = p.summary?.trim() || null;
      }
    } else if (e.type === "run_failed") {
      if ((e.payload as RunFailedPayload).product_landed === true) {
        partial = true;
      }
    } else if (e.type === "tool_use_end") {
      const meta = (e.payload as { metadata?: { partial_failure?: boolean } })
        .metadata;
      if (meta?.partial_failure === true) partial = true;
    }
  }
  return { partial, summary };
}

export function collectRetrySignal(events: readonly SSEEvent[]): {
  retryable: boolean | null;
  retryAfter: number | null;
} {
  let retryable: boolean | null = null;
  let retryAfter: number | null = null;
  for (const e of events) {
    if (e.type === "run_failed") {
      const p = e.payload as RunFailedPayload;
      if (typeof p.retryable === "boolean") retryable = p.retryable;
      if (typeof p.retry_after === "number") retryAfter = p.retry_after;
    } else if (e.type === "error") {
      const after = (e.payload as ErrorPayload).context?.retry_after;
      if (typeof after === "number") retryAfter = after;
    }
  }
  return { retryable, retryAfter };
}

function collectWireResult(
  events: readonly SSEEvent[],
): TurnOutcomeKind | null {
  let result: TurnOutcomeKind | null = null;
  for (const e of events) {
    if (e.type === "message_end") {
      result = wireTurnResultFromPayload(e.payload as MessageEndPayload);
    }
  }
  return result;
}

function collectChrome(events: readonly SSEEvent[]): {
  finishReason: string | null;
  errorCode: string | undefined;
  errorMessage: string | undefined;
} {
  let finishReason: string | null = null;
  let errorCode: string | undefined;
  let errorMessage: string | undefined;
  for (const e of events) {
    if (e.type === "error") {
      const p = e.payload as ErrorPayload;
      errorCode = p.code;
      errorMessage = withLocalRecoveryMoment(p.message, {
        code: p.code,
        context: p.context,
      });
    } else if (e.type === "message_end") {
      finishReason = (e.payload as MessageEndPayload).finish_reason;
    }
  }
  return { finishReason, errorCode, errorMessage };
}

function recoveryFor(opts: {
  kind: TurnOutcomeKind;
  errorCode?: string | null;
  errorMessage?: string | null;
  credentialSource?: string | null;
  retryable: boolean | null;
  retryAfter: number | null;
  finishReason?: string | null;
}): TurnRecovery {
  const action = errorActionForCode(opts.errorCode ?? undefined, {
    credentialSource: opts.credentialSource,
    message: opts.errorMessage,
  });
  if (action) {
    return { kind: "configure", label: action.label, href: action.href };
  }
  if (opts.retryable === true) {
    if (opts.retryAfter != null && opts.retryAfter > 0) {
      return { kind: "wait", retryAfterSec: opts.retryAfter };
    }
    return { kind: "wait_unknown" };
  }
  if (opts.kind === "ok" || opts.kind === "paused") return { kind: "none" };
  if (
    opts.kind === "error" &&
    opts.finishReason === "interrupted" &&
    !opts.errorCode &&
    !opts.errorMessage?.trim()
  ) {
    return { kind: "send_next" };
  }
  if (opts.finishReason === "max_rounds") return { kind: "none" };
  if (opts.kind === "error" || opts.kind === "partial") {
    return { kind: "retry" };
  }
  return { kind: "none" };
}

export type TurnOutcomeInput = {
  skip?: boolean;
  wireResult?: TurnOutcomeKind | null;
  finishReason?: string | null;
  content?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  credentialSource?: string | null;
  deliveryState?: string | null;
  deliverySummary?: string | null;
  productLanded?: boolean;
  partialFailureMeta?: boolean;
  events?: readonly SSEEvent[];
  runs?: readonly { productLanded?: boolean | null }[];
  retryable?: boolean | null;
  retryAfter?: number | null;
  hasDedicatedPauseOrAskUi?: boolean;
  paused?: boolean | null;
  projectedStatus?: string | null;
  /** Visible team graph: strip is the primary verdict (bubble must not repeat it). */
  hasTeamGraph?: boolean;
};

export function resolveTurnOutcome(input: TurnOutcomeInput): TurnOutcome {
  if (input.skip) {
    return {
      kind: "ok",
      notice: null,
      reason: null,
      surface: "none",
      recovery: { kind: "none" },
      hideEmptyBubble: false,
    };
  }

  const events = input.events ?? [];
  const hasTeamGraph = resolveHasTeamGraph(input);
  const wire = input.wireResult ?? collectWireResult(events);
  const chrome = events.length ? collectChrome(events) : null;
  const finishReason = input.finishReason ?? chrome?.finishReason ?? null;
  const errorCode = input.errorCode ?? chrome?.errorCode ?? null;
  const errorMessage = input.errorMessage ?? chrome?.errorMessage ?? null;
  const retry =
    input.retryable != null || input.retryAfter != null
      ? {
          retryable: input.retryable ?? null,
          retryAfter: input.retryAfter ?? null,
        }
      : collectRetrySignal(events);
  const bits = collectPartialBits({
    deliveryState: input.deliveryState,
    deliverySummary: input.deliverySummary,
    productLanded: input.productLanded,
    partialFailureMeta: input.partialFailureMeta,
    events,
    runs: input.runs,
  });

  const pausedLike =
    wire === "paused" ||
    input.paused === true ||
    finishReason === "paused" ||
    input.projectedStatus === "paused";

  let kind: TurnOutcomeKind;
  if (wire) {
    kind = wire;
  } else if (
    finishReason === "cancelled" &&
    !errorCode &&
    !errorMessage?.trim()
  ) {
    kind = "ok";
  } else if (bits.partial) {
    kind = "partial";
  } else if (
    pausedLike &&
    (input.hasDedicatedPauseOrAskUi || !errorMessage?.trim())
  ) {
    kind = "paused";
  } else if (
    errorCode ||
    errorMessage?.trim() ||
    finishReason === "error" ||
    finishReason === "unproductive" ||
    finishReason === "degraded" ||
    finishReason === "interrupted"
  ) {
    kind = "error";
  } else if (pausedLike) {
    kind = "paused";
  } else {
    kind = "ok";
  }

  const recovery = recoveryFor({
    kind,
    errorCode,
    errorMessage,
    credentialSource: input.credentialSource,
    retryable: retry.retryable,
    retryAfter: retry.retryAfter,
    finishReason,
  });

  if (kind === "ok") {
    const emptyMaxRounds =
      finishReason === "max_rounds" && !(input.content ?? "").trim();
    if (emptyMaxRounds) {
      const maxNotice = resolveEmptyFailureNotice({
        content: "",
        finishReason: "max_rounds",
      });
      return {
        kind,
        notice: maxNotice,
        reason: null,
        surface: maxNotice ? (hasTeamGraph ? "strip" : "error") : "none",
        recovery: { kind: "none" },
        errorCode,
        retryable: retry.retryable,
        hideEmptyBubble: false,
      };
    }
    return {
      kind,
      notice: null,
      reason: null,
      surface: "none",
      recovery,
      errorCode,
      retryable: retry.retryable,
      hideEmptyBubble:
        finishReason === "cancelled" && !(input.content ?? "").trim(),
    };
  }

  if (kind === "paused") {
    // Attested CEO continue pause. Gate pauses (`outcome=null`) stay surface-none.
    if (wire === "paused") {
      return {
        kind: "paused",
        notice: PAUSED_VERDICT,
        reason: errorMessage?.trim() || null,
        surface: "paused",
        recovery: { kind: "continue" },
        errorCode,
        retryable: retry.retryable,
        hideEmptyBubble: false,
      };
    }
    return {
      kind: "paused",
      notice: null,
      reason: null,
      surface: "none",
      recovery: { kind: "none" },
      errorCode,
      retryable: retry.retryable,
      hideEmptyBubble: false,
    };
  }

  if (kind === "partial") {
    if (errorCode === "LLM_RATE_LIMIT") {
      const why = errorMessage?.trim() || RATE_LIMIT_WHY;
      return {
        kind,
        notice: why,
        reason: null,
        surface: "composer",
        recovery,
        errorCode,
        retryable: retry.retryable,
        hideEmptyBubble: false,
      };
    }
    return {
      kind,
      // 条用 kind 画「部分完成」；交付摘要不进用户面（呈现甲已撤）。
      notice: null,
      reason: null,
      surface: hasTeamGraph ? "strip" : "none",
      recovery,
      errorCode,
      retryable: retry.retryable,
      hideEmptyBubble: false,
    };
  }

  const rawNotice = resolveEmptyFailureNotice({
    content: input.content,
    finishReason,
    errorMessage,
    hasDedicatedPauseOrAskUi: input.hasDedicatedPauseOrAskUi,
  });
  const notice = rawNotice
    ? withTransientWaitHint(rawNotice, retry.retryable, retry.retryAfter)
    : null;

  let surface: TurnOutcomeSurface = "none";
  if (notice) {
    if (recovery.kind === "send_next" && hasTeamGraph) {
      surface = "composer";
    } else {
      surface = hasTeamGraph ? "strip" : "error";
    }
  }

  return {
    kind: "error",
    notice,
    reason: null,
    surface,
    recovery: notice ? recovery : { kind: "none" },
    errorCode,
    retryable: retry.retryable,
    hideEmptyBubble: false,
  };
}

/** Journal-first entry: live SSE / history `runs.events`. */
export function resolveTurnOutcomeFromJournal(opts: {
  events: readonly SSEEvent[];
  content?: string | null;
  skip?: boolean;
  hasDedicatedPauseOrAskUi?: boolean;
  paused?: boolean | null;
  finishReason?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  credentialSource?: string | null;
  deliveryState?: string | null;
  deliverySummary?: string | null;
  runs?: readonly { productLanded?: boolean | null }[];
  projectedStatus?: string | null;
  wireResult?: TurnOutcomeKind | null;
  hasTeamGraph?: boolean;
}): TurnOutcome {
  return resolveTurnOutcome(opts);
}

export function turnOwnsUserFacingOutlet(outcome: TurnOutcome): boolean {
  return outcome.surface !== "none";
}

/** CEO rate-limit pause face (not a gate ResumeCard). */
export function isCeoContinuePause(outcome: TurnOutcome): boolean {
  return outcome.kind === "paused" && outcome.surface === "paused";
}

/** Team strip: product_landed failures are 部分完成, not 「N 失败」. */
export function teamFailureProgressBit(
  workers: readonly { status: string; productLanded?: boolean | null }[],
): string | null {
  const failed = workers.filter((r) => r.status === "failed");
  if (failed.length === 0) return null;
  if (failed.some((r) => r.productLanded === true)) return PARTIAL_NOTICE;
  return `${failed.length} 失败`;
}

export type TeamStripFace = {
  title: string;
  mark: "run" | "ok" | "err" | "paused" | "muted";
  phase: boolean;
};

/**
 * Strip title/mark from the turn arbiter, not raw `status==="failed"`.
 * Running/paused lifecycle still follows fold status (live chrome).
 */
export function teamStripFace(
  status: TurnStatus | null | undefined,
  outcome?: Pick<TurnOutcome, "kind" | "recovery"> | null,
): TeamStripFace {
  if (status === "running") {
    return { title: "", mark: "run", phase: true };
  }
  if (status === "paused") {
    return { title: "", mark: "paused", phase: true };
  }
  if (outcome?.kind === "partial") {
    return { title: PARTIAL_NOTICE, mark: "ok", phase: false };
  }
  if (outcome?.recovery.kind === "send_next") {
    return { title: INTERRUPTED_STRIP_TITLE, mark: "err", phase: false };
  }
  if (outcome?.kind === "error") {
    return { title: FAILED_STRIP_TITLE, mark: "err", phase: false };
  }
  if (status === "failed") {
    return { title: FAILED_STRIP_TITLE, mark: "err", phase: false };
  }
  if (status === "cancelled") {
    return { title: STOPPED_STRIP_TITLE, mark: "muted", phase: false };
  }
  return { title: "", mark: "ok", phase: false };
}

/** Bubble banner (not team strip, not CEO continue card, not composer hint). */
export function turnOutcomeShowsBubbleBanner(outcome: TurnOutcome): boolean {
  return outcome.surface === "error";
}

/** Input-area light hint. Judgment is `surface=composer` — leaves must not re-test rate-limit. */
export function turnOutcomeShowsComposerHint(outcome: TurnOutcome): boolean {
  return outcome.surface === "composer";
}

/** Conformance envelope — judge encoding is ``hasTeamStrip`` + ``supportPackHost``. */
export function toConformanceTurnVerdict(args: {
  outcome: TurnOutcome;
  hasTeamStrip: boolean;
  failedToolHintNames?: readonly string[];
}): ProjectedTurnVerdict {
  return {
    kind: args.outcome.kind,
    hideEmptyBubble: args.outcome.hideEmptyBubble,
    notice: args.outcome.notice,
    hasTeamStrip: args.hasTeamStrip,
    supportPackHost: supportPackHostFromSurface(args.outcome.surface),
    failedToolHintNames: [...(args.failedToolHintNames ?? [])],
  };
}

/** Fold → mobile turnOutcome snapshot for the shared conformance sidecar. */
export function turnVerdictFromProjected(
  events: readonly SSEEvent[],
  projected: ProjectedTurn,
): ProjectedTurnVerdict {
  const outcome = resolveTurnOutcome({
    events,
    content: projected.content,
    finishReason: projected.finishReason,
    errorCode: projected.error?.code,
    errorMessage: projected.error?.message,
    deliveryState: projected.deliveryStatus?.state,
    deliverySummary: projected.deliveryStatus?.summary,
    runs: projected.runs,
    projectedStatus: projected.status,
    wireResult: projected.outcome,
    hasTeamGraph: projectedHasTeamGraph(projected),
    hasDedicatedPauseOrAskUi: projectedHasDedicatedPauseUi(projected),
  });
  const failed = collectFailedToolNames(projected.process);
  const hintNames = shouldShowUnproductiveToolFailureHint({
    finishReason: projected.finishReason,
    content: projected.content,
    failedToolNames: failed,
  })
    ? failed
    : [];
  return toConformanceTurnVerdict({
    outcome,
    hasTeamStrip: projectedHasTeamGraph(projected),
    failedToolHintNames: hintNames,
  });
}
