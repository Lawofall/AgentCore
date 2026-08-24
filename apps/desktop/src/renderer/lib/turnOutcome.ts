/**
 * Turn-level error arbitrator: five independent failure pipes collapse into
 * one {@link TurnOutcome}. UI renders one primary status + one recovery action.
 *
 * Pipes: `execution.status`, per-run `run_failed`, `message.error`,
 * `finishReason`, `conversation.error`.
 *
 * Flag contract (rest-of-states; `kind=paused` is a frozen read-only path):
 * consumers must paint from these flags, not from `execution.status` or leaf
 * ifs. See {@link TurnOutcome}.
 */

import {
  GENERIC_EMPTY_FAILURE_MESSAGE,
  LLM_RATE_LIMIT_MESSAGE,
  LLM_RATE_LIMIT_WHY,
  TURN_CANCELLED_EMPTY_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  errorActionForCode,
  isEmptyResponseUserSurface,
  resolveAssistantFailureFace,
} from "@/lib/errors";
import type { ErrorAction } from "@/lib/errors";
import { withRecoveryMoment } from "@/lib/recoveryMoment";
import { shouldShowUnproductiveToolFailureHint } from "@/lib/unproductiveToolFailureHint";
import { assistantProjectionId } from "@/stores/conversation";
import type { Message } from "@/stores/conversation";
import type {
  ExecutionRuntime,
  ExecutionStatus,
  RunFrame,
} from "@/stores/execution";
import type { SSEEvent } from "@/types/events";
import {
  isRateLimitFamilyCode,
  rateLimitRetrySuppressed,
} from "@agentcore/contract-types";
import type { ProjectedTurn } from "@agentcore/protocol-conformance/projectedTurn";
import {
  type ProjectedTurnVerdict,
  projectedFailedToolNames,
  projectedHasDedicatedPauseUi,
  projectedHasTeamGraph,
} from "@agentcore/protocol-conformance/turnVerdict";

/** Server-attested / client-derived turn conclusion.
 * Attested `paused` = CEO rate-limit continue (not a checkpoint). */
export type TurnOutcomeKind = "ok" | "partial" | "paused" | "error";

export type TurnRecoveryKind =
  | "none"
  | "send_next"
  | "wait_then_retry"
  | "configure"
  | "resume"
  | "continue";

export type TurnRecovery = {
  kind: TurnRecoveryKind;
  /** Composer hint / wait copy / configure button label. */
  label: string | null;
  href?: string;
  /** Seconds to wait when known; `null` = transient but duration unknown. */
  retryAfterSec?: number | null;
};

export type StructuredErr = {
  code?: string | null;
  message?: string | null;
  context?: {
    retry_after?: number | null;
    empty_diagnosis?: string;
    recovery_at?: string | null;
    reset_at?: string | null;
    credential_source?: "user" | "platform" | string | null;
  } | null;
} | null;

/** Where「复制排查包」hangs. Primary verdict only — never two hosts. */
export type TurnSupportPackHost =
  | "none"
  | "bubble"
  | "strip"
  | "composer"
  | "session";

export type TurnFailedRun = {
  id: string;
  kind?: string | null;
  status: string;
  error?: string | null;
  errorCode?: string | null;
  retryable?: boolean | null;
  retryAfter?: number | null;
  productLanded?: boolean | null;
};

export type TurnOutcomeInput = {
  isStreaming?: boolean;
  content?: string | null;
  reasoning?: string | null;
  processLength?: number;
  citationCount?: number;
  turnWarning?: boolean;
  finishReason?: string | null;
  messageError?: StructuredErr;
  runsError?: StructuredErr;
  usageError?: StructuredErr;
  executionStatus?: ExecutionStatus | string | null;
  runs?: readonly TurnFailedRun[];
  deliveryState?: string | null;
  deliverySummary?: string | null;
  conversationError?: string | null;
  conversationErrorAction?: ErrorAction | null;
  /**
   * Live pause/ask/plan_review/team_preview still waiting. Callers must pass
   * **pending only** — a resolved stub is history, not a hang.
   */
  hasDedicatedPauseOrAskUi?: boolean;
  hasPendingDecision?: boolean;
  /** Server-attested kind (`message_end.outcome` / REST). Gate pause leaves this null. */
  attestedKind?: TurnOutcomeKind | null;
  /**
   * Collaboration-graph StatusStrip exists for this turn. Callers that own a
   * strip (StatusStrip always; AssistantMessage when `slot.plan` / process
   * `team` marker) must pass this — the arbitrator does not infer it, so a
   * half-landed consumer wave cannot blank the bubble card while the strip
   * still follows `execution.status`.
   */
  hasTeamStrip?: boolean;
  /** BYOK vs platform — `errorActionForCode` CTA split. */
  credentialSource?: string | null;
};

export type TurnOutcome = {
  kind: TurnOutcomeKind;
  message: string | null;
  code: string | null;
  recovery: TurnRecovery;
  face: { code: string; message: string } | null;
  /**
   * Assistant bubble error card. Exclusive with strip / composer-hint /
   * silent-ok. Single-chat owns the red card; team graph does not repeat it.
   */
  showBubbleBanner: boolean;
  /**
   * Composer light hint. Rest-of-states that light this (error card off):
   * empty interrupt (`send_next`) and partial+rate-limit (`wait_then_retry`).
   * Sending the next message clears it. `kind=paused` never lights this.
   */
  showComposerHint: boolean;
  /**
   * Gate for ComposerSendErrorNotice **sessionError** (not `composerError`).
   * True only when `conversationError` copy exists AND this turn has no other
   * primary verdict. Block D: `suppressSession={!showSessionBanner}`.
   */
  showSessionBanner: boolean;
  showFinishReasonChip: boolean;
  /**
   * Assistant footer chrome (copy / cost / feedback / 重新生成).
   * Named recovery (`configure` / `wait_then_retry` / `send_next`) hides it.
   * When recovery is `none` on an error/partial, footer 重新生成 is the unique
   * retry control — including team-strip turns that closed the bubble card.
   */
  showFooter: boolean;
  /** Empty user-stop, nothing else to show: omit the bubble. kind is `ok`. */
  hideEmptyBubble: boolean;
  /**
   * StatusStrip FailureStrip. Follow this, not `execution.status==="failed"`.
   * Off for empty interrupt (`send_next` stays on composer) and user-stop.
   */
  showStripFailure: boolean;
  /**
   * StatusStrip「已停止」. kind stays `ok` (stop is not an error). Follow this,
   * not `execution.status==="cancelled"` — rate-limit/partial must not paint 已停止.
   */
  showStripStopped: boolean;
  /**
   * StatusStrip idle chrome (n/m only). Empty interrupt (`send_next`) — verdict
   * stays on the composer; do not spin「进行中」or paint 已停止 / 失败.
   */
  showStripIdle: boolean;
  /**
   * Preflight `turn_warning` banner. Off for user-stop — strip / half-finished
   * body is the verdict; do not restyle Stop as a warning. Streaming may still
   * light this so the soft-gate appears before settle.
   */
  showTurnWarning: boolean;
  /**
   * 「复制排查包」host. Follows the unique verdict: empty interrupt and
   * partial+rate-limit → composer; hard fail → bubble/strip; paused → none.
   */
  supportPackHost: TurnSupportPackHost;
};

export const PARTIAL_STATUS_LABEL = "部分完成";
export const PAUSED_STATUS_LABEL = "已暂停";
export const PAUSED_CONTINUE_LABEL = "继续";

/** Attested Retry-After wait copy — recovery constraint, not a second verdict. */
export function attestedWaitHint(retryAfterSec: number): string {
  return `约 ${Math.round(retryAfterSec)} 秒后可继续`;
}

function partialCardReason(
  face: { code: string; message: string } | null,
  retryAfterSec: number | null | undefined,
): string | null {
  if (face?.code !== "LLM_RATE_LIMIT") return null;
  if (retryAfterSec != null && retryAfterSec > 0) {
    return `${LLM_RATE_LIMIT_WHY}${attestedWaitHint(retryAfterSec)}。`;
  }
  return LLM_RATE_LIMIT_WHY;
}

/** Attested CEO rate-limit pause — one verdict + POST …/continue. */
export function isAttestedPauseContinue(outcome: {
  kind: TurnOutcomeKind;
  recovery: Pick<TurnRecovery, "kind">;
}): boolean {
  return outcome.kind === "paused" && outcome.recovery.kind === "continue";
}

const WIRE_KINDS = new Set<TurnOutcomeKind>([
  "ok",
  "partial",
  "paused",
  "error",
]);

/** Accept only the server-attested enum; unknown / missing → null (local fallback). */
export function parseTurnOutcomeKind(value: unknown): TurnOutcomeKind | null {
  if (typeof value === "string" && WIRE_KINDS.has(value as TurnOutcomeKind)) {
    return value as TurnOutcomeKind;
  }
  return null;
}

/** Last `message_end.outcome` in a journal / live event list. */
export function attestedKindFromEvents(
  events: readonly { type: string; payload?: unknown }[] | null | undefined,
): TurnOutcomeKind | null {
  if (!events) return null;
  let found: TurnOutcomeKind | null = null;
  for (const e of events) {
    if (e.type !== "message_end") continue;
    const raw = (e.payload as { outcome?: unknown } | undefined)?.outcome;
    const parsed = parseTurnOutcomeKind(raw);
    if (parsed) found = parsed;
  }
  return found;
}

const TRANSIENT_CODES = new Set(["LLM_RATE_LIMIT", "LLM_TIMEOUT"]);

function retryAfterNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return value;
  }
  return null;
}

function isCancelCode(code: string | null | undefined): boolean {
  return code === "TURN_CANCELLED";
}

function emptyShell(input: TurnOutcomeInput): boolean {
  if ((input.content ?? "").trim()) return false;
  if ((input.reasoning ?? "").trim()) return false;
  if ((input.processLength ?? 0) > 0) return false;
  if (input.turnWarning) return false;
  if ((input.citationCount ?? 0) > 0) return false;
  return true;
}

function pauseSignal(input: TurnOutcomeInput): boolean {
  if (input.attestedKind === "paused") return true;
  if (input.finishReason === "paused") return true;
  if (input.executionStatus === "paused") return true;
  if (input.hasDedicatedPauseOrAskUi) return true;
  if (input.hasPendingDecision) return true;
  return false;
}

function isPartial(input: TurnOutcomeInput): boolean {
  if (input.attestedKind === "partial") return true;
  if (input.deliveryState === "partial") return true;
  return (input.runs ?? []).some(
    (r) => r.status === "failed" && r.productLanded === true,
  );
}

function winningRetryable(
  input: TurnOutcomeInput,
  code: string | null,
): { retryable: boolean; retryAfterSec: number | null } {
  let retryAfterSec: number | null = retryAfterNumber(
    input.messageError?.context?.retry_after,
  );
  let sawTrue = false;
  let sawFalse = false;
  for (const run of input.runs ?? []) {
    const after = retryAfterNumber(run.retryAfter);
    if (after != null && retryAfterSec == null) retryAfterSec = after;
    if (run.retryable === true) sawTrue = true;
    if (run.retryable === false) sawFalse = true;
  }
  if (isRateLimitFamilyCode(code) && rateLimitRetrySuppressed(retryAfterSec)) {
    return { retryable: false, retryAfterSec };
  }
  if (sawTrue) return { retryable: true, retryAfterSec };
  if (sawFalse) return { retryable: false, retryAfterSec };
  if (code && TRANSIENT_CODES.has(code)) {
    return { retryable: true, retryAfterSec };
  }
  return { retryable: false, retryAfterSec };
}

function isUserStopTurn(
  input: TurnOutcomeInput,
  face: { code: string } | null,
): boolean {
  return isCancelCode(face?.code) || input.finishReason === "cancelled";
}

function deriveKind(
  input: TurnOutcomeInput,
  face: { code: string; message: string } | null,
): TurnOutcomeKind {
  const attested = parseTurnOutcomeKind(input.attestedKind);
  const userStopped = isUserStopTurn(input, face);
  // Attested paused can linger on the same bubble after ask→resume→Stop.
  // User-stop is not a hang — do not keep kind=paused (that path paints the
  // cancel face as a bubble warning under the team graph).
  if (attested && !(attested === "paused" && userStopped)) return attested;

  const structuredFailure =
    face != null &&
    !isCancelCode(face.code) &&
    face.code !== "TURN_INTERRUPTED";
  if (pauseSignal(input) && !structuredFailure && !userStopped) return "paused";
  // User-stop face wins over leftover delivery.partial / productLanded.
  // Rate-limit (or any non-cancel face) on a cancelled *status* still follows
  // the winning face — do not let execution.status paint「已停止」over kind.
  if (
    isPartial(input) &&
    input.executionStatus !== "completed" &&
    !isCancelCode(face?.code)
  ) {
    return "partial";
  }

  if (isCancelCode(face?.code)) return "ok";

  if (
    face ||
    input.executionStatus === "failed" ||
    (input.conversationError ?? "").trim() ||
    (input.runs ?? []).some((r) => r.status === "failed")
  ) {
    return "error";
  }
  return "ok";
}

function deriveRecovery(
  input: TurnOutcomeInput,
  face: { code: string; message: string } | null,
  kind: TurnOutcomeKind,
): TurnRecovery {
  if (kind === "paused") {
    if (parseTurnOutcomeKind(input.attestedKind) === "paused") {
      const { retryAfterSec } = winningRetryable(input, face?.code ?? null);
      return {
        kind: "continue",
        label: PAUSED_CONTINUE_LABEL,
        retryAfterSec,
      };
    }
    return { kind: "resume", label: null };
  }
  if (kind === "ok") {
    return { kind: "none", label: null };
  }

  const code = face?.code ?? null;
  const configure = errorActionForCode(code ?? undefined, {
    message: face?.message,
    credentialSource:
      input.credentialSource ??
      input.messageError?.context?.credential_source ??
      null,
  });
  if (configure) {
    return {
      kind: "configure",
      label: configure.label,
      href: configure.href,
    };
  }

  const { retryable, retryAfterSec } = winningRetryable(input, code);
  if (retryable) {
    return {
      kind: "wait_then_retry",
      // Unknown duration: product copy already says 请稍后再试 — do not invent seconds.
      label: retryAfterSec == null ? LLM_RATE_LIMIT_MESSAGE : null,
      retryAfterSec,
    };
  }

  if (
    face?.code === "TURN_INTERRUPTED" ||
    (input.finishReason === "interrupted" &&
      (!face || face.code === "TURN_INTERRUPTED"))
  ) {
    return {
      kind: "send_next",
      label: TURN_INTERRUPTED_EMPTY_MESSAGE,
    };
  }

  return { kind: "none", label: null };
}

function quietFlags(): Pick<
  TurnOutcome,
  | "showBubbleBanner"
  | "showComposerHint"
  | "showSessionBanner"
  | "showFinishReasonChip"
  | "showFooter"
  | "hideEmptyBubble"
  | "showStripFailure"
  | "showStripStopped"
  | "showStripIdle"
  | "showTurnWarning"
  | "supportPackHost"
> {
  return {
    showBubbleBanner: false,
    showComposerHint: false,
    showSessionBanner: false,
    showFinishReasonChip: false,
    showFooter: false,
    hideEmptyBubble: false,
    showStripFailure: false,
    showStripStopped: false,
    showStripIdle: false,
    showTurnWarning: false,
    supportPackHost: "none",
  };
}

function namedRecoveryHidesFooter(kind: TurnRecoveryKind): boolean {
  return (
    kind === "configure" || kind === "wait_then_retry" || kind === "send_next"
  );
}

function withOutcomeMoment(
  input: TurnOutcomeInput,
  text: string | null,
): string | null {
  if (!text) return null;
  return withRecoveryMoment(text, {
    recovery_at: input.messageError?.context?.recovery_at,
    reset_at: input.messageError?.context?.reset_at,
    context: input.messageError?.context,
  });
}

/**
 * Collapse the five failure pipes into one turn conclusion.
 * Callers render {@link TurnOutcome.kind} as the primary status and
 * {@link TurnOutcome.recovery} as the sole recovery action.
 */
export function arbitrateTurnOutcome(input: TurnOutcomeInput): TurnOutcome {
  if (input.isStreaming) {
    return {
      kind: "ok",
      message: null,
      code: null,
      recovery: { kind: "none", label: null },
      face: null,
      ...quietFlags(),
      // Soft-gate may arrive before message_end; Stop is not settled yet.
      showTurnWarning: Boolean(input.turnWarning),
    };
  }

  const face = resolveAssistantFailureFace({
    content: input.content,
    isStreaming: false,
    error: input.messageError,
    runsError: input.runsError,
    usageError: input.usageError,
    finishReason: input.finishReason,
    hasDedicatedPauseOrAskUi: input.hasDedicatedPauseOrAskUi,
  });

  const kind = deriveKind(input, face);
  const recovery = deriveRecovery(input, face, kind);
  const attestedContinue = isAttestedPauseContinue({ kind, recovery });

  const hideEmptyBubble = emptyShell(input) && isCancelCode(face?.code);
  const sessionCopy = (input.conversationError ?? "").trim();
  const hasTeamStrip = Boolean(input.hasTeamStrip);
  const hasBody = Boolean((input.content ?? "").trim());

  const chipOwnedByBanner = isEmptyResponseUserSurface({
    code: face?.code ?? input.messageError?.code,
    emptyDiagnosis: input.messageError?.context?.empty_diagnosis,
    message: face?.message ?? input.messageError?.message,
  });
  const fr = input.finishReason ?? undefined;
  const chipMeta =
    fr === "max_rounds" || fr === "degraded" || fr === "unproductive";

  // `kind=paused` is a frozen read-only path — flag formulas must stay byte-stable.
  if (kind === "paused") {
    // Cancel face must not become a warning card if kind is still paused
    // (attested leftover / resolved-card pauseSignal). Stop is not an error.
    const showBubbleBanner =
      face != null &&
      !hideEmptyBubble &&
      !attestedContinue &&
      !isCancelCode(face.code);
    const showComposerHint =
      recovery.kind === "send_next" &&
      !input.hasPendingDecision &&
      !input.hasDedicatedPauseOrAskUi &&
      !attestedContinue;
    const showSessionBanner = Boolean(
      sessionCopy && !showBubbleBanner && !attestedContinue,
    );
    const showFinishReasonChip = Boolean(
      chipMeta && !chipOwnedByBanner && !showBubbleBanner && !attestedContinue,
    );
    const showFooter =
      !attestedContinue &&
      !hideEmptyBubble &&
      face?.code !== "TURN_INTERRUPTED" &&
      (hasBody ||
        Boolean(showBubbleBanner && face && !isCancelCode(face.code)));
    let message: string | null = null;
    if (attestedContinue) {
      // Why only — wait belongs on the Continue control when Retry-After is attested.
      message =
        face?.code === "LLM_RATE_LIMIT"
          ? LLM_RATE_LIMIT_WHY
          : (face?.message ?? (sessionCopy || null));
    } else if (showBubbleBanner) {
      message = face?.message ?? null;
    } else if (showSessionBanner) {
      message = sessionCopy;
    }
    return {
      kind,
      message,
      code: face?.code ?? null,
      recovery,
      face,
      showBubbleBanner,
      showComposerHint,
      showSessionBanner,
      showFinishReasonChip,
      showFooter,
      hideEmptyBubble,
      showStripFailure: false,
      showStripStopped: false,
      showStripIdle: false,
      showTurnWarning: Boolean(input.turnWarning),
      supportPackHost: "none",
    };
  }

  // Partial + upstream 429: why hangs on this hint; the strip stays a scoreboard.
  // Do not require wait_then_retry — retryable=false still owes the same sentence.
  const composerOwnsRateLimitWhy =
    kind === "partial" && face?.code === "LLM_RATE_LIMIT";
  const showComposerHint =
    (recovery.kind === "send_next" || composerOwnsRateLimitWhy) &&
    !input.hasPendingDecision &&
    !input.hasDedicatedPauseOrAskUi;
  const showStripStopped =
    hasTeamStrip && kind === "ok" && isCancelCode(face?.code);
  const showStripFailure =
    hasTeamStrip && kind === "error" && recovery.kind !== "send_next";
  const showStripIdle = hasTeamStrip && recovery.kind === "send_next";
  const showBubbleBanner =
    face != null &&
    !hideEmptyBubble &&
    kind !== "partial" &&
    kind !== "ok" &&
    recovery.kind !== "send_next" &&
    !hasTeamStrip &&
    !isCancelCode(face.code);
  const showSessionBanner = Boolean(
    sessionCopy &&
      !showBubbleBanner &&
      !showComposerHint &&
      !showStripFailure &&
      !showStripStopped &&
      !hideEmptyBubble &&
      kind !== "partial" &&
      !isCancelCode(face?.code) &&
      sessionCopy !== TURN_CANCELLED_EMPTY_MESSAGE,
  );
  const showFinishReasonChip = Boolean(
    chipMeta && !chipOwnedByBanner && !showBubbleBanner,
  );
  // User-stop is not a warning. Follow finishReason / cancel face — not a
  // leaf `turnWarning === "已停止"` string match (copy drift would leak it back).
  const showTurnWarning =
    Boolean(input.turnWarning) &&
    !isCancelCode(face?.code) &&
    fr !== "cancelled" &&
    !hideEmptyBubble;
  const showFooter =
    !hideEmptyBubble &&
    !namedRecoveryHidesFooter(recovery.kind) &&
    (hasBody ||
      Boolean(
        (showBubbleBanner || showStripFailure) &&
          face &&
          !isCancelCode(face.code),
      ));

  let supportPackHost: TurnSupportPackHost = "none";
  if (showBubbleBanner) supportPackHost = "bubble";
  else if (showComposerHint) supportPackHost = "composer";
  else if (showStripFailure || (kind === "partial" && hasTeamStrip)) {
    supportPackHost = "strip";
  } else if (showSessionBanner) supportPackHost = "session";

  let message: string | null = null;
  if (kind === "partial") {
    message =
      partialCardReason(face, recovery.retryAfterSec) ??
      input.deliverySummary?.trim() ??
      null;
  } else if (showBubbleBanner || showStripFailure) {
    message = withOutcomeMoment(input, face?.message ?? null);
  } else if (showComposerHint) {
    message = recovery.label ?? face?.message ?? TURN_INTERRUPTED_EMPTY_MESSAGE;
  } else if (showSessionBanner) {
    message = withOutcomeMoment(input, sessionCopy);
  } else if (kind === "error") {
    message = GENERIC_EMPTY_FAILURE_MESSAGE;
  }

  return {
    kind,
    message,
    code: face?.code ?? null,
    recovery,
    face,
    showBubbleBanner,
    showComposerHint,
    showSessionBanner,
    showFinishReasonChip,
    showFooter,
    hideEmptyBubble,
    showStripFailure,
    showStripStopped,
    showStripIdle,
    showTurnWarning,
    supportPackHost,
  };
}

/** Map journal / live `run_failed` frames into arbitrator run rows. */
export function failedRunsFromFrames(
  frames: readonly RunFrame[] | null | undefined,
): TurnFailedRun[] {
  if (!frames) return [];
  const out: TurnFailedRun[] = [];
  for (const f of frames) {
    if (f.kind !== "run_failed") continue;
    out.push({
      id: f.runId,
      status: "failed",
      error: f.error,
      errorCode: f.errorCode ?? null,
      retryable: f.retryable ?? null,
      retryAfter: f.retryAfter ?? null,
      productLanded: f.productLanded ?? null,
    });
  }
  return out;
}

export function turnOutcomeInputFromMessage(
  message: Pick<
    Message,
    | "content"
    | "reasoning"
    | "process"
    | "citations"
    | "turnWarning"
    | "isStreaming"
    | "finishReason"
    | "error"
    | "runs"
    | "usage"
    | "outcome"
  >,
  extras?: Partial<TurnOutcomeInput>,
): TurnOutcomeInput {
  const fr = message.finishReason ?? message.runs?.finishReason ?? null;
  const fromMessage = parseTurnOutcomeKind(message.outcome);
  const fromExtras = parseTurnOutcomeKind(extras?.attestedKind);
  return {
    isStreaming: message.isStreaming,
    content: message.content,
    reasoning: message.reasoning,
    processLength: message.process?.length ?? 0,
    citationCount: message.citations?.length ?? 0,
    turnWarning: Boolean(message.turnWarning),
    finishReason: fr,
    messageError: message.error ?? null,
    runsError: message.runs?.error ?? null,
    usageError: message.usage?.error ?? null,
    ...extras,
    // Message-stamped outcome wins over slot/extras — extras must not drop it.
    attestedKind: fromMessage ?? fromExtras ?? null,
    credentialSource:
      extras?.credentialSource ??
      message.error?.context?.credential_source ??
      null,
  };
}

/** Fill execution-slot pipes for a live / reloaded assistant bubble. */
export function executionPipesFromSlot(
  slot: ExecutionRuntime | null | undefined,
): Pick<
  TurnOutcomeInput,
  | "executionStatus"
  | "runs"
  | "deliveryState"
  | "deliverySummary"
  | "attestedKind"
> {
  if (!slot) {
    return {
      executionStatus: null,
      runs: [],
      deliveryState: null,
      deliverySummary: null,
      attestedKind: null,
    };
  }
  return {
    executionStatus: slot.status,
    runs: failedRunsFromFrames(slot.frames),
    deliveryState: slot.deliveryStatus?.state ?? null,
    deliverySummary: slot.deliveryStatus?.summary ?? null,
    attestedKind: parseTurnOutcomeKind(slot.attestedOutcome),
  };
}

export function turnOutcomeForAssistant(
  message: Message,
  slot: ExecutionRuntime | null | undefined,
  extras?: Partial<TurnOutcomeInput>,
): TurnOutcome {
  return arbitrateTurnOutcome(
    turnOutcomeInputFromMessage(message, {
      ...executionPipesFromSlot(slot),
      ...extras,
    }),
  );
}

/** Collaboration-graph StatusStrip exists for this assistant turn. */
export function assistantHasTeamStrip(
  message: Pick<Message, "process">,
  slot?: Pick<ExecutionRuntime, "plan"> | null,
): boolean {
  return (
    Boolean(slot?.plan) ||
    (message.process ?? []).some((s) => s.kind === "team")
  );
}

export function projectionSlotKey(message: Message): string {
  return assistantProjectionId(message);
}

/** Conformance envelope — judge encoding is ``hasTeamStrip`` + ``supportPackHost``. */
export function toConformanceTurnVerdict(args: {
  outcome: TurnOutcome;
  hasTeamStrip: boolean | null;
  failedToolHintNames?: readonly string[];
}): ProjectedTurnVerdict {
  return {
    kind: args.outcome.kind,
    hideEmptyBubble: args.outcome.hideEmptyBubble,
    notice: args.outcome.message,
    hasTeamStrip: args.hasTeamStrip,
    supportPackHost: args.outcome.supportPackHost,
    failedToolHintNames: [...(args.failedToolHintNames ?? [])],
  };
}

/** Fold → desktop turnOutcome snapshot for the shared conformance sidecar. */
export function turnVerdictFromProjected(
  _events: readonly SSEEvent[],
  projected: ProjectedTurn,
): ProjectedTurnVerdict {
  const hasTeamStrip = projectedHasTeamGraph(projected);
  const outcome = arbitrateTurnOutcome({
    content: projected.content,
    reasoning: projected.reasoning,
    processLength: projected.process.length,
    citationCount: projected.citations.length,
    turnWarning: Boolean(projected.turnWarning),
    finishReason: projected.finishReason,
    messageError: projected.error,
    attestedKind: projected.outcome,
    deliveryState: projected.deliveryStatus?.state ?? null,
    deliverySummary: projected.deliveryStatus?.summary ?? null,
    hasTeamStrip,
    hasDedicatedPauseOrAskUi: projectedHasDedicatedPauseUi(projected),
    executionStatus: projected.status,
    runs: projected.runs
      .filter((r) => r.status === "failed")
      .map((r) => ({
        id: r.id,
        status: r.status,
        error: r.error,
        productLanded: r.productLanded,
      })),
  });
  const failed = projectedFailedToolNames(projected);
  const hintNames = shouldShowUnproductiveToolFailureHint({
    finishReason: projected.finishReason ?? undefined,
    content: projected.content,
    failedToolNames: failed,
  })
    ? failed
    : [];
  return toConformanceTurnVerdict({
    outcome,
    hasTeamStrip,
    failedToolHintNames: hintNames,
  });
}
