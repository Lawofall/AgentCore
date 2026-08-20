/**
 * @agentcore/protocol-fold-kit — shared protocol fold *constants / pure predicates*
 * for desktop + mobile. Does **not** ship `fold(events)→ProjectedTurn`.
 *
 * 「用时」跨度（{@link turnElapsedMs}）也在这里：它是同名指标，两端算的必须是同一个量。
 * 同理还有回合收益口径（`teamGain`：队友互相把关）、按人干预的可用性判定
 * （`runIntervene`：能不能只停 / 只改这一个队员，不能时给哪句原因）——同一句文案、同一个数，
 * 两端各写一份就是在给「用时」那次分叉留同样的缝。
 *
 * Allowed under cross-platform-frontend: protocol constants yes; shared fold
 * implementation no. Gate remains `pnpm conformance`.
 *
 * 协议文案层：空失败脸与降级 chip、挂问、导出 chrome、恢复时刻子句、
 * 排查包 extras 成文（桌面/手机逐字同一份）。
 */

export {
  ORCHESTRATION_TOOLS,
  isOrchestrationTool,
  MARKER_STANDIN_TOOLS,
  isMarkerStandinTool,
} from "./tools";

export {
  FINISH_TO_STATUS,
  turnStatusFromFinish,
  type FinishMappedStatus,
} from "./finishStatus";

export {
  PRODUCED_OUTCOMES,
  coerceProducedOutcome,
  eventsHavePartialProduct,
  resolveTurnOutcome,
  type OutcomeWireEvent,
  type ProducedTurnOutcome,
  type TurnOutcome,
} from "./turnOutcome";

export {
  RUN_FRAME_EVENT_TYPES,
  isRunFrameEvent,
  turnElapsedMs,
  type TimedWireEvent,
} from "./turnElapsed";

export {
  interveneAckText,
  isLiveRunStatus,
  runRedirectGate,
  runStopGate,
  type InterveneAck,
  type InterveneGate,
  type InterveneRunStatus,
} from "./runIntervene";

export {
  COLLAB_SUMMARY_TOOLTIP,
  formatCollabSummary,
  type CollabCounts,
} from "./teamGain";

export {
  HANGING_QUESTION_CAPTION,
  HANGING_QUESTION_CTA,
  HANGING_QUESTION_DEFAULT_HINT,
  HANGING_QUESTION_DETACHED_HINT,
  formatHangingDefault,
  type HangingAssumptionCopy,
} from "./hangingQuestion";

export {
  EMPTY_RESPONSE_CHIP_LABELS,
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  degradedFinishChipLabel,
  isEmptyResponseUserSurface,
} from "./errorCopy";

export {
  recoveryMomentRecoveryClause,
  recoveryMomentResetClause,
} from "./recoveryMomentCopy";

export {
  MESSAGE_EXPORT_DELIVERABLE_HEADING,
  MESSAGE_EXPORT_PROCESS_HEADING,
  MESSAGE_EXPORT_REASONING_HEADING,
  MESSAGE_EXPORT_STEP_CHROME,
  MESSAGE_EXPORT_TOOL_STATUS_SUFFIX,
  type MessageCopyMode,
} from "./messageExportCopy";

export {
  SUPPORT_DIAGNOSTIC_PREVIEW_MAX,
  formatSupportDiagnosticText,
  sanitizeSupportDiagnosticPreview,
  supportDiagnosticExtrasFromError,
  type SupportDiagnosticErrorExtras,
  type SupportDiagnosticIds,
  type SupportErrorContext,
} from "./supportDiagnosticExtras";

export {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheDisplayBilledAsMiss,
  cacheUsageDisplay,
  displayCacheMiss,
  isOmittedCacheSplit,
  type CacheSplitCounts,
} from "./cacheUsageDisplay";
