import { getAutonomy } from "@/api/autonomy";
import { listBrowserSessions } from "@/api/browserSessions";
import { getTokens } from "@/api/client";
import {
  type MemoryUpdate,
  type MessageDetail,
  createConversation,
  deleteConversation,
  getConversation,
  getMessages,
  setConversationModelProfile,
} from "@/api/conversations";
import { getFolder } from "@/api/folders";
import { sendMidFlightMessage } from "@/api/midFlight";
import {
  getLastModelProfileId,
  resolveDisplayProfile,
  setLastModelProfileId,
  useModelProfiles,
} from "@/api/modelProfiles";
import {
  DEFAULT_PERMISSION_AXES,
  type PermissionAxes,
  axesShortLabel,
  normalizeAxes,
  recipeToAxes,
} from "@/api/permissionAxes";
import { resolveStageCardStream } from "@/api/stageCard";
import {
  type ResumeTurnBody,
  type TeamPreviewAmendments,
  continueStream,
  followConversation,
  regenerateStream,
  resumeStream,
  streamMessage,
} from "@/api/stream";
import {
  type PausedTurnSummary,
  type PendingInteractionSummary,
  type TurnRecovery,
  getRecovery,
  stopConversation,
} from "@/api/turn";
import { getMessageCostDisplay } from "@/api/usage";
import { RecoveredChip } from "@/components/AssistantMessageFooter";
import {
  AssistantContent,
  SupportDiagnosticCopyButton,
  shouldShowTeamGraph,
} from "@/components/AssistantView";
import { BrowserLiveSheet } from "@/components/BrowserLiveSheet";
import type { OpenBrowserLiveOpts } from "@/components/BrowserLoginDecisionCard";
import { CollapsibleUserText } from "@/components/CollapsibleUserText";
import { ComposerMentionSheet } from "@/components/ComposerMentionSheet";
import { ComposerMoreSheet } from "@/components/ComposerMoreSheet";
import { ConversationDrawer } from "@/components/ConversationDrawer";
import {
  type DraftFolder,
  DraftFolderChip,
} from "@/components/DraftFolderChip";
import { FileArtifactsCard } from "@/components/FileArtifactsCard";
import { MemoryUpdateCard } from "@/components/MemoryUpdateCard";
import { ModelPicker } from "@/components/ModelPicker";
import { PauseCard } from "@/components/PauseCard";
import { PausedContinueCard } from "@/components/PausedContinueCard";
import { PermissionAxesSheet } from "@/components/PermissionAxesSheet";
import { QueuedTurnsBar } from "@/components/QueuedTurnsBar";
import { RemoteSettledCards } from "@/components/RemoteSettledCards";
import { ResumeCard } from "@/components/ResumeCard";
import { StageCard } from "@/components/StageCard";
import { EscalationAnswer } from "@/components/TeamView";
import { TurnOutcomeActions } from "@/components/TurnOutcomeActions";
import { UserBubbleChips } from "@/components/UserBubbleChips";
import { VoiceButton, VoiceRecordingBar } from "@/components/VoiceInput";
import { clearAiAttentionForConversation } from "@/lib/aiAttention";
import { useAppForeground } from "@/lib/appLifecycle";
import {
  type MessageAttachment,
  finalizeAttachmentsForSend,
  hasSendableDraft,
  prepareAttachment,
} from "@/lib/attachments";
import { readDraftFolderState } from "@/lib/cloudFolder";
import {
  applyColdInteractionWireEvent,
  bindEmptyColdMessageId,
  clearColdInteractions,
  getColdInteraction,
  idFromColdRequiredPayload,
  isColdResumeKind,
  kindFromColdRequiredEvent,
  kindFromColdResolvedEvent,
  listColdPending,
  markColdDeferred,
  markColdOrphaned,
  markColdResolved,
  markColdSubmitting,
  rekeyColdMessageId,
  reopenColdPending,
  upsertColdRequired,
  useColdInteractions,
} from "@/lib/coldInteractions";
import {
  type ColdResumeHost,
  pausedSummaryToRequiredPayload,
  resolveColdBindHostId,
  selectVisibleColdResumes,
} from "@/lib/coldResume";
import {
  type PendingAgentMention,
  attachmentDraftKey,
  toOutgoingAgentMentions,
} from "@/lib/composerMention";
import { composerTrailingSlots } from "@/lib/composerTrailing";
import { noteConversationStreamEvent } from "@/lib/conversationListCache";
import {
  type ErrorAction,
  StreamHttpError,
  degradedFinishChipLabel,
  describeStreamHttpError,
  emptyChatCopy,
  errorActionForCode,
  isPausedFrameGone,
  isUnstartedSendRefusal,
} from "@/lib/errors";
import { resolveArtifactsForTurn } from "@/lib/fileArtifacts";
import {
  type FollowTurnCursor,
  planFollowIdle,
  planFollowSegment,
  readSegmentHead,
  turnMessageId,
} from "@/lib/followTurns";
import { placeMemoryUpdates } from "@/lib/memoryAnchors";
import {
  type MessageDelivery,
  defaultDelivery,
  isLiveInterruptible,
} from "@/lib/messageDelivery";
import { currencySymbol } from "@/lib/money";
import {
  type QueuedTurnEntry,
  listQueuedTurns,
  removeQueuedTurn,
  upsertQueuedTurn,
} from "@/lib/queuedTurns";
import {
  QUEUE_DROPPED_HINT,
  reconcileQueuedTurns,
} from "@/lib/reconcileQueuedTurns";
import {
  clearLiveTurnEvents,
  dropRunningAssistantTail,
  removeLiveTurn,
} from "@/lib/reconnectLiveTurn";
import { withLocalRecoveryMoment } from "@/lib/recoveryMoment";
import {
  createHarvestRefreshScheduler,
  dropSettledLiveTurns,
} from "@/lib/refreshAfterExecutionCompleted";
import {
  isForeignSettlement,
  markLocalSettlement,
  noteRemoteSettlement,
  noteRemoteSettlementFromReceipt,
  resetRemoteSettlements,
  settlementFromResolvedEvent,
} from "@/lib/remoteSettlement";
import { prepareResumePausedTurn } from "@/lib/resumePausedTurn";
import {
  STOP_FAILED_MESSAGE,
  type StopUiPhase,
  allowsEventWhileStopping,
  isStopBusy,
  isStopConfirmEvent,
  reduceStopPhase,
  stopButtonLabel,
} from "@/lib/stopLifecycle";
import {
  type SupportDiagnosticIds,
  precedingUserMessageId,
  supportIdsFromEvents,
} from "@/lib/supportDiagnostics";
import { firstCollabAtMs } from "@/lib/time";
import {
  type TurnOutcome,
  isCeoContinuePause,
  resolveTurnOutcomeFromJournal,
  turnOutcomeShowsBubbleBanner,
  turnOutcomeShowsComposerHint,
  turnOwnsUserFacingOutlet,
} from "@/lib/turnOutcome";
import { useComposerMention } from "@/lib/useComposerMention";
import { useStickScroll } from "@/lib/useStickScroll";
import { useVoiceInput } from "@/lib/useVoiceInput";
import { inspectZeroOutputSendRollback } from "@/lib/zeroOutputSendRollback";
import {
  type EscalationSlotEsc,
  extractCoordinationWait,
  extractEscalationSlots,
  extractEvidenceLedger,
  extractExecutionDetached,
  extractGraphAppendActKinds,
  extractGraphAppendAuthorizedBy,
  extractHotDecisionTraces,
  extractPrevExecutionIds,
  extractRunToolCalls,
  extractStageCardTraces,
  extractToolPhases,
  extractWorkerToolPhases,
  fold,
} from "@/protocol/fold";
import { extractTeamPreviewTraces } from "@/protocol/teamPreviewTraces";
import type {
  CheckpointDecision,
  DebateNarrativeRound,
  ErrorPayload,
  MessageEndPayload,
  MessageStartPayload,
  ResumeDeferredPayload,
  SSEEvent,
  TurnQueueCancelledPayload,
  TurnQueueStartedPayload,
  TurnQueuedPayload,
  TurnWarningPayload,
  UsageBreakdown,
} from "@agentcore/contract-types";
import type {
  ProjectedInteraction,
  ProjectedTurn,
} from "@agentcore/protocol-conformance";
import { turnElapsedMs } from "@agentcore/protocol-fold-kit";
import {
  ArrowDown,
  Folder,
  Loader2,
  Menu,
  Send,
  Square,
  SquarePen,
} from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

// One-shot handoff from a draft send (at `/`) to the freshly-created conversation's first
// stream (at `/c/:id`). 直接对话: the 对话 tab opens a draft (no server conversation); the
// first send lazily creates one, routes to /c/:id, and the remounted ChatPage picks this up
// to POST+stream that first message (rather than re-attaching to a not-yet-existing run).
// Lives outside React so it survives the / → /c/:id remount; cleared on consume, and a hard
// refresh simply finds it empty (the unsent draft is gone — acceptable).
let pendingFirstSend: {
  id: string;
  text: string;
  attachments: MessageAttachment[];
  agentMentions: PendingAgentMention[];
  folder: DraftFolder | null;
} | null = null;

// Class A/B 回滚后拆空会话回到 `/`：ChatPage 在 /c/:id → / 会整页重挂，useState
// error / 还回的草稿都会丢。与 pendingFirstSend 同款模块变量跨重挂；消费后清空。
// 错误只走错误条，禁止写进 textarea。startDraft 成功后幂等键已置 null——拆后不要钉回。
// folder 对齐桌面 restoreAfterUnstartedRefusal：拆回草稿后 chip / 下次 create 仍带同一 folder_id。
let pendingEmptyRollback: {
  error: ChatError;
  text: string;
  attachments: MessageAttachment[];
  agentMentions: PendingAgentMention[];
  folder: DraftFolder | null;
} | null = null;

/** 测例隔离：清掉 / ↔ /c/:id 交棒，避免用例互相钉死草稿或错误条。 */
export function __resetChatPageHandoffForTests(): void {
  pendingFirstSend = null;
  pendingEmptyRollback = null;
}

// A turn streamed this session. `userText === null` for a turn whose user bubble already
// lives in the persisted history (a reattach on reopen / a durable resume) — only its
// assistant side streams live; a fresh send carries its own user text.
interface Turn {
  id: string;
  userText: string | null;
  events: SSEEvent[];
  // Display-only chips for files this turn carried (text inline and/or workspace resident).
  attachments?: { name: string; truncated?: boolean }[];
  /** Conversation-page ``@`` role chips (soft mention; not attachment kind). */
  agentMentions?: { agentId: string; role: string }[];
}

/** 主时间线用户气泡（排队期不插泡；出队开跑后再出现）。 */
function UserTurnBubble({ turn }: { turn: Turn }) {
  if (turn.userText === null) return null;
  return (
    <div className="bubble user">
      <CollapsibleUserText contentKey={turn.userText}>
        {turn.userText}
      </CollapsibleUserText>
      <UserBubbleChips
        attachments={turn.attachments ?? []}
        agentMentions={turn.agentMentions ?? []}
      />
    </div>
  );
}

/** Live 回合时钟：优先 message_start.timestamp，否则首帧。 */
function extractTurnClock(events: SSEEvent[]): string | null {
  for (const e of events) {
    if (e.type === "message_start" && e.timestamp) return e.timestamp;
  }
  return events[0]?.timestamp ?? null;
}

/** message_end + error 旁路元数据（用量 / 轮次 / 收尾 / 诊断）— 不入 ProjectedTurn. */
function extractTurnChrome(events: SSEEvent[]): {
  usage: UsageBreakdown | null;
  rounds: number | null;
  durationMs: number | null;
  finishReason: string | null;
  emptyDiagnosis: string | undefined;
  bodyKind: string | undefined;
  baseUrl: string | undefined;
  errorCode: string | undefined;
  errorMessage: string | undefined;
  credentialSource: string | null | undefined;
} {
  let usage: UsageBreakdown | null = null;
  let rounds: number | null = null;
  let durationMs: number | null = null;
  let finishReason: string | null = null;
  let emptyDiagnosis: string | undefined;
  let bodyKind: string | undefined;
  let baseUrl: string | undefined;
  let errorCode: string | undefined;
  let errorMessage: string | undefined;
  let credentialSource: string | null | undefined;
  for (const e of events) {
    if (e.type === "error") {
      const p = e.payload as ErrorPayload;
      errorCode = p.code;
      // 429 / 配额闸门的恢复时刻在这里就换成本机时区（下游的红卡 / 空泡文案 / 收尾提示
      // 都读这一份），拿不到结构化时刻则原样是服务端那句。
      errorMessage = withLocalRecoveryMoment(p.message, {
        code: p.code,
        context: p.context,
      });
      emptyDiagnosis = p.context?.empty_diagnosis;
      bodyKind = p.context?.body_kind;
      baseUrl = p.context?.base_url;
      credentialSource = p.context?.credential_source;
    }
    if (e.type === "message_end") {
      const p = e.payload as MessageEndPayload;
      finishReason = p.finish_reason;
      rounds = typeof p.rounds === "number" ? p.rounds : null;
      durationMs =
        typeof p.duration_ms === "number" && p.duration_ms > 0
          ? p.duration_ms
          : null;
      if (p.usage) {
        usage = {
          input: p.usage.input_tokens,
          output: p.usage.output_tokens,
          reasoning: p.usage.reasoning_tokens,
          cache_hit: p.usage.cache_hit_tokens,
          cache_miss: p.usage.cache_miss_tokens,
        };
      }
    }
  }
  return {
    usage,
    rounds,
    durationMs,
    finishReason,
    emptyDiagnosis,
    bodyKind,
    baseUrl,
    errorCode,
    errorMessage,
    credentialSource,
  };
}

/** Error extras for「复制排查包」(SSE ErrorContext; cold RunError only has code). */
function supportErrorExtras(opts: {
  errorCode?: string | null;
  emptyDiagnosis?: string | null;
  bodyKind?: string | null;
  baseUrl?: string | null;
}): Pick<
  SupportDiagnosticIds,
  "errorCode" | "emptyDiagnosis" | "bodyKind" | "baseUrl" | "stream"
> {
  const errorCode = opts.errorCode?.trim() || undefined;
  const emptyDiagnosis = opts.emptyDiagnosis?.trim() || undefined;
  const bodyKind = opts.bodyKind?.trim() || undefined;
  const baseUrl = opts.baseUrl?.trim() || undefined;
  const stream =
    !!emptyDiagnosis || errorCode === "LLM_EMPTY_RESPONSE" ? true : undefined;
  return { errorCode, emptyDiagnosis, bodyKind, baseUrl, stream };
}

/** Build 排查包 ids for a history assistant row (REST trace_id + journal execution_id). */
function historySupportIds(
  m: MessageDetail,
  conversationId: string | null,
  extras?: ReturnType<typeof supportErrorExtras>,
  userMessageId?: string | null,
): SupportDiagnosticIds {
  if (m.runs?.events?.length) {
    return supportIdsFromEvents(conversationId, m.runs.events, {
      messageId: m.id,
      traceId: m.trace_id,
      userMessageId,
    });
  }
  let executionId: string | undefined;
  if (m.runs?.process) {
    for (const s of m.runs.process) {
      if (s.kind === "team" && s.execution_id) {
        executionId = s.execution_id;
        break;
      }
    }
  }
  return {
    conversationId,
    messageId: m.id,
    userMessageId: userMessageId || undefined,
    traceId: m.trace_id ?? undefined,
    executionId,
    ...extras,
  };
}

// A run keeps running detached after a dropped connection (执行与请求解耦 C1 · slice 1a),
// so a transport drop reconnects (rejoins the live run), never resends.
const RECONNECT_BANNER = "连接中断，回合仍在后台继续。点「重连」继续查看。";

/** 对话级订阅正常关流后自动重挂的最短存活门槛（服务端心跳 15s；两拍以上才算「长驻过」）。
 *  老后端不认 `follow`、空闲直接 204 —— 那是立刻结束，够不着这道门槛，不会热循环。 */
const FOLLOW_REHANG_MIN_MS = 30_000;

/** A turn-level error with an optional one-tap reconnect (a held SSE that dropped while
 *  the run lives on), or a config remedy (e.g.「去配置」→ 模型配置 for LLM_KEY_REQUIRED).
 *  /stop 失败只出诚实文案，可再点停止按钮（无「重试停止」专属路径）。 */
interface ChatError {
  text: string;
  reconnect?: boolean;
  action?: ErrorAction;
  /** Turn-scoped: hide this bar when the assistant bubble already owns the outcome. */
  fromTurn?: boolean;
  supportIds?: SupportDiagnosticIds;
}

function withTurnSupport(
  err: ChatError,
  conversationId: string | undefined,
  events: readonly SSEEvent[],
): ChatError {
  return {
    ...err,
    fromTurn: true,
    supportIds: supportIdsFromEvents(conversationId, events),
  };
}

/** User-facing tone: config remedy (去配置) → needs-you / accent; else recoverable gray. */
function errorSurfaceClass(
  kind: "bar" | "inline-actions",
  needsYou: boolean,
): string {
  return needsYou ? `error ${kind} needs-you` : `error ${kind}`;
}

/** The user's 停止 (abort button), never surfaced as an error. */
function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** Format an integer nano cost as a money caption (1 unit = 1e9). Returns null for
 *  0 / unknown so a free turn shows nothing, never「¥0.00」(§7.5). BYOK estimates get ≈.
 *  符号取自后端随金额下发的 `currency`（平台记账 CNY / BYOK 社区估算 USD）——**无汇率换算**。 */
function formatCost(
  nano: number | null | undefined,
  estimated = false,
  currency?: string | null,
): string | null {
  if (!nano || nano <= 0) return null;
  const symbol = currencySymbol(currency);
  const amount = nano / 1e9;
  const body =
    amount < 0.01
      ? `<${symbol}0.01`
      : `${symbol}${amount.toFixed(amount < 0.1 ? 4 : 2)}`;
  return estimated ? `≈${body} 自带密钥·估算` : body;
}

type CachedDisplayMoney = {
  nano: number;
  estimated: boolean;
  /** 该笔金额的币种（ISO code）；渲染端据此挑符号，禁止按 pricing_source 猜。 */
  currency?: string | null;
  /** BYOK 社区价目未命中（pricing_source=unpriced）：显式标注，金额不出数。 */
  unpriced?: boolean;
};

/** 未计价标注文案（拍板 2026-07-20，与桌面 COST_UNPRICED_LABEL 同口径）。 */
const COST_UNPRICED_LABEL = "自带密钥·未计价";

// A reloaded turn carries no cost in its MessageDetail (the ledger is the truth source);
// fetch it lazily per message, cached module-wide so re-renders / re-opens don't refetch.
const costCache = new Map<string, CachedDisplayMoney>();
const costInflight = new Set<string>();

/** Lazily fetch a persisted turn's cost when its bubble scrolls into view (Intersection
 *  Observer — avoids an open-time request storm over a whole window). Returns the cached
 *  display money; supplementary, so a failure just leaves the row uncosted. */
function useLazyMessageCost(messageId: string): {
  ref: React.RefObject<HTMLDivElement | null>;
  money: CachedDisplayMoney | null;
} {
  const ref = useRef<HTMLDivElement>(null);
  const [money, setMoney] = useState<CachedDisplayMoney | null>(
    () => costCache.get(messageId) ?? null,
  );
  useEffect(() => {
    if (!messageId) return;
    if (costCache.has(messageId)) {
      setMoney(costCache.get(messageId) ?? null);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      obs.disconnect();
      if (costCache.has(messageId)) {
        setMoney(costCache.get(messageId) ?? null);
        return;
      }
      if (costInflight.has(messageId)) return;
      costInflight.add(messageId);
      getMessageCostDisplay(messageId)
        .then((t) => {
          if (!t) return;
          costCache.set(messageId, t);
          setMoney(t);
        })
        .finally(() => costInflight.delete(messageId));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [messageId]);
  return { ref, money };
}

function recoveredApprovalPending(
  a: PendingInteractionSummary,
): Extract<ProjectedInteraction, { kind: "approval" }> | null {
  if (a.kind !== "approval") return null;
  const p = a.payload ?? {};
  return {
    kind: "approval",
    id: a.id,
    status: "pending",
    toolCallId: typeof p.tool_call_id === "string" ? p.tool_call_id : a.id,
    toolName: typeof p.tool_name === "string" ? p.tool_name : "tool",
    arguments: (p.arguments as Record<string, unknown>) ?? {},
  };
}

function recoveredStageCard(
  a: PendingInteractionSummary,
): Extract<ProjectedInteraction, { kind: "stage_card" }> | null {
  if (a.kind !== "stage_card") return null;
  const p = a.payload ?? {};
  const sides = Array.isArray(p.sides)
    ? (p.sides as Array<Record<string, unknown>>).map((s) => ({
        key: typeof s.key === "string" ? s.key : "",
        name: typeof s.name === "string" ? s.name : "",
        stance: typeof s.stance === "string" ? s.stance : "",
      }))
    : [];
  const ptrs = Array.isArray(p.fact_pointers)
    ? p.fact_pointers.filter((x): x is string => typeof x === "string")
    : [];
  return {
    kind: "stage_card",
    id: a.id,
    status: "pending",
    motion: typeof p.motion === "string" ? p.motion : "",
    sides,
    form: typeof p.form === "string" ? p.form : "debate",
    rationale: typeof p.rationale === "string" ? p.rationale : "",
    factPointers: ptrs,
    thorough: p.thorough !== false,
    maxRounds: Number(p.max_rounds ?? 5) || 5,
    note: typeof p.note === "string" ? p.note : null,
  };
}

/** Cold recovery · pending escalation → EscalationAnswer card body. */
function recoveredEscalation(a: PendingInteractionSummary): {
  id: string;
  runId: string;
  esc: EscalationSlotEsc;
} | null {
  if (a.kind !== "escalation") return null;
  const p = a.payload ?? {};
  if (p.awaiting === "ceo") return null;
  const question = typeof p.question === "string" ? p.question.trim() : "";
  if (!question) return null;
  const assumption = typeof p.assumption === "string" ? p.assumption : "";
  const runId = typeof p.run_id === "string" ? p.run_id : "";
  const kindRaw = p.kind;
  const kind =
    kindRaw === "scope" || kindRaw === "dep" ? kindRaw : ("normal" as const);
  return {
    id: a.id,
    runId,
    esc: {
      question,
      assumption,
      blocking: true,
      status: "pending",
      answer: null,
      kind,
      ...(p.awaiting === "user" ? { awaiting: "user" as const } : {}),
      ...(p.browser_login === true ? { browserLogin: true as const } : {}),
    },
  };
}

/** One-line status derived from the projected turn — proves the fold drives the UI
 * (进度 / 工具 are read off ProjectedTurn, not re-parsed from events). A `paused` turn
 * returns null: its actionable surface owns the view instead — an `approval` pause shows the
 * PauseCard above the composer, while 挂起即收口 (②, Phase 3) a checkpoint / plan_review pause
 * finalizes the turn and shows the durable ResumeCard below it. */
function summarize(p: ProjectedTurn): string | null {
  if (p.status === "paused") return null;
  if (p.runs.length > 0) {
    const running = p.runs.filter((r) => r.status === "running").length;
    return `团队 ${p.progress.completed}/${p.progress.total} 完成${running ? ` · ${running} 进行中` : ""}`;
  }
  const tool = [...p.process].reverse().find((s) => s.kind === "tool");
  if (tool && tool.kind === "tool" && tool.status === "running")
    return `正在调用 ${tool.tool_name}…`;
  // Failed-turn copy lives on the outcome banner (单一出口); do not add「出错了」.
  return null;
}

/** 本回合最后一张图的 `execution_id`（按人干预的提交目标）——跨回合续接时以新图为准。 */
function teamExecutionId(process: ProjectedTurn["process"]): string | null {
  for (let i = process.length - 1; i >= 0; i -= 1) {
    const step = process[i];
    if (step.kind === "team" && step.execution_id) return step.execution_id;
    if (step.kind === "graph_append" && step.execution_id) {
      return step.execution_id;
    }
  }
  return null;
}

function extractTurnWarning(events: SSEEvent[]): string | null {
  for (const e of events) {
    if (e.type === "turn_warning") {
      return (e.payload as TurnWarningPayload).message;
    }
  }
  return null;
}

/** Live / journal：``message_start.message_id``（客户端 turn.id 是本地 UUID，不能当云 messageId）。 */
const extractMessageId = turnMessageId;

/** 空泡失败红卡豁免：该回合已是 paused，或时间线已有 checkpoint / ResumeCard 面。 */
function dedicatedPauseOrAskUi(opts: {
  paused?: boolean | null;
  finishReason?: string | null;
  projectedStatus?: string | null;
  process?: readonly { kind: string }[] | null;
}): boolean {
  if (
    opts.paused ||
    opts.finishReason === "paused" ||
    opts.projectedStatus === "paused"
  ) {
    return true;
  }
  return (opts.process ?? []).some(
    (s) =>
      s.kind === "checkpoint" ||
      s.kind === "plan_review" ||
      s.kind === "team_preview",
  );
}

function TurnOutcomeBanner({
  outcome,
  supportIds,
  onRetry,
  onContinue,
  continueLocked,
}: {
  outcome: TurnOutcome;
  supportIds: SupportDiagnosticIds;
  onRetry?: () => void;
  onContinue?: () => Promise<void> | void;
  continueLocked?: boolean;
}) {
  if (isCeoContinuePause(outcome)) {
    return (
      <PausedContinueCard
        reason={outcome.reason}
        onContinue={onContinue}
        locked={continueLocked}
      />
    );
  }
  if (!turnOutcomeShowsBubbleBanner(outcome)) return null;
  if (!outcome.notice) return null;
  return (
    <TurnOutcomeActions
      outcome={outcome}
      supportIds={supportIds}
      onRetry={onRetry}
    />
  );
}

function AssistantBubble({
  turn,
  live,
  conversationId,
  onFill,
  onOpenBrowserLive,
  onRetry,
  onContinue,
  continueLocked,
}: {
  turn: Turn;
  live: boolean;
  conversationId: string | null;
  onFill: (text: string) => void;
  onOpenBrowserLive?: (opts?: OpenBrowserLiveOpts) => void;
  onRetry?: () => void;
  onContinue?: () => Promise<void> | void;
  continueLocked?: boolean;
}) {
  const p = useMemo(() => fold(turn.events), [turn.events]);
  const messageId = useMemo(() => extractMessageId(turn.events), [turn.events]);
  const chrome = useMemo(() => extractTurnChrome(turn.events), [turn.events]);
  // 主清单优先 delivery_status；缺字段时回落 process/events（A1 旁路同源）。
  const { list: artifacts, review: reviewArtifacts } = useMemo(
    () =>
      resolveArtifactsForTurn({
        deliveryStatus: p.deliveryStatus,
        process: p.process,
        events: turn.events,
      }),
    [p.deliveryStatus, p.process, turn.events],
  );
  // 升级时间线槽（统一时间线二期）: escalation_id → card body（旁路；golden escalations 不加 id）。
  const escalationSlots = useMemo(
    () => extractEscalationSlots(turn.events),
    [turn.events],
  );
  // 热审批/委派授权痕迹 (D3): resolved 轻行内容（旁路读原始事件）。
  const hotTraces = useMemo(
    () => extractHotDecisionTraces(turn.events),
    [turn.events],
  );
  const stageCardTraces = useMemo(
    () => extractStageCardTraces(turn.events),
    [turn.events],
  );
  const teamPreviewTraces = useMemo(
    () => extractTeamPreviewTraces(turn.events),
    [turn.events],
  );
  // 工具执行阶段进度 (联网搜索前端展示优化): tool_call_id→阶段，旁路读原始事件（不入 ProjectedTurn），
  // 让运行中的工具（web_search）显示「正在检索/排队中/改用备用引擎」而非干等。已结束的工具自动清空。
  const toolPhases = useMemo(
    () => extractToolPhases(turn.events),
    [turn.events],
  );
  const workerToolPhases = useMemo(
    () => extractWorkerToolPhases(turn.events),
    [turn.events],
  );
  const waitProgress = useMemo(
    () => extractCoordinationWait(turn.events),
    [turn.events],
  );
  const detached = useMemo(
    () => extractExecutionDetached(turn.events),
    [turn.events],
  );
  // 阻塞式求决策「待你拍板」: runId→escalation id from interactions[] (P3 · 按 id 精确提交).
  const pendingEscalations = useMemo(() => {
    const map = new Map<string, string>();
    for (const i of p.interactions) {
      if (i.kind === "escalation" && i.status === "pending") {
        map.set(i.runId, i.id);
      }
    }
    return map;
  }, [p.interactions]);
  // 队员工具明细 (RunDetail): runId→worker 工具调用 (旁路读原始事件，不入 ProjectedTurn)，喂给
  // 团队视图的队员详情面；实时与回放同一条接线（history 走 HistoryAssistant 里的同一提取器）。
  const runToolCalls = useMemo(
    () => extractRunToolCalls(turn.events),
    [turn.events],
  );
  // 两通道：调研回合台账（fold `#rN`）vs 辩论场级台账（extract `#eN`）。
  const turnEvidenceLedger = p.evidenceLedger;
  const debateEvidenceLedger = useMemo(
    () => extractEvidenceLedger(turn.events),
    [turn.events],
  );
  const graphAppendActKinds = useMemo(
    () => extractGraphAppendActKinds(turn.events),
    [turn.events],
  );
  const graphAppendAuthorizedBy = useMemo(
    () => extractGraphAppendAuthorizedBy(turn.events),
    [turn.events],
  );
  const prevExecutionIds = useMemo(
    () => extractPrevExecutionIds(turn.events),
    [turn.events],
  );
  const meta = summarize(p);
  const clockIso = extractTurnClock(turn.events);
  const isMulti = p.runs.length > 0;
  const hasTeamGraph = shouldShowTeamGraph(p.runs);
  const empty =
    !isMulti && p.process.length === 0 && !p.content && !p.reasoning;
  const finishReason = live ? null : (chrome.finishReason ?? p.finishReason);
  const pauseUi = dedicatedPauseOrAskUi({
    finishReason,
    projectedStatus: p.status,
    process: p.process,
  });
  const outcome = resolveTurnOutcomeFromJournal({
    events: turn.events,
    content: p.content,
    skip: live,
    hasDedicatedPauseOrAskUi: pauseUi,
    finishReason,
    errorCode: chrome.errorCode,
    errorMessage: chrome.errorMessage,
    credentialSource: chrome.credentialSource,
    deliveryState: p.deliveryStatus?.state,
    deliverySummary: p.deliveryStatus?.summary,
    runs: p.runs,
    projectedStatus: p.status,
    hasTeamGraph,
  });
  const pauseFace = isCeoContinuePause(outcome);
  const outcomeNotice = pauseFace ? null : outcome.notice;
  // 回合总账 — populated by message_end (null while streaming, so it appears on finish).
  // BYOK: billed total is 0; estimated_total may carry a community-catalog estimate.
  // 币种随金额走：记账读 currency，BYOK 估算读 estimated_currency（美元社区价目）。
  const turnMoney =
    p.cost && p.cost.total > 0
      ? { nano: p.cost.total, estimated: false, currency: p.cost.currency }
      : p.cost?.estimated_total && p.cost.estimated_total > 0
        ? {
            nano: p.cost.estimated_total,
            estimated: true,
            currency: p.cost.estimated_currency ?? p.cost.currency,
          }
        : null;
  const cost = turnMoney
    ? formatCost(turnMoney.nano, turnMoney.estimated, turnMoney.currency)
    : p.cost?.pricing_source === "unpriced"
      ? COST_UNPRICED_LABEL
      : null;
  const turnWarning = p.turnWarning;
  const supportIds = supportIdsFromEvents(conversationId, turn.events, {
    messageId,
  });
  const team = isMulti
    ? {
        agents: p.agents,
        runs: p.runs,
        progress: p.progress,
        acts: p.acts,
        teamNotes: p.teamNotes,
        status: p.status,
        conversationId,
        executionId: teamExecutionId(p.process),
        pendingEscalations,
        escalationsInteractive: live,
        runToolCalls,
        workerToolPhases,
        evidenceLedger: debateEvidenceLedger,
        elapsedMs: turnElapsedMs(turn.events),
        startedAtMs: firstCollabAtMs(turn.events),
        waitProgress,
        detached,
        outcome,
        supportIds,
        onRetry,
      }
    : undefined;
  const finishDiagnosis = degradedFinishChipLabel(
    chrome.emptyDiagnosis,
    chrome.errorMessage,
  );
  // 空停止：聊天时间线不占「已停止」行（有团队面时 empty=false，走 TeamView）。
  if (
    empty &&
    outcome.hideEmptyBubble &&
    !live &&
    !pauseFace &&
    !turnWarning &&
    artifacts.length === 0
  ) {
    return null;
  }
  return (
    <>
      <div className="bubble assistant">
        {turnWarning && <div className="turn-warning">{turnWarning}</div>}
        {empty && !outcomeNotice ? (
          <span className="muted">{live ? "…" : ""}</span>
        ) : !empty ? (
          <AssistantContent
            process={p.process}
            content={p.content}
            reasoning={p.reasoning}
            citations={p.citations}
            evidenceLedger={turnEvidenceLedger}
            isStreaming={live}
            messageId={messageId}
            captainContext={p.captainContext}
            team={team}
            debate={p.debate}
            debateRounds={p.debateRounds}
            escalationSlots={escalationSlots}
            hotTraces={hotTraces}
            stageCardTraces={stageCardTraces}
            teamPreviewTraces={teamPreviewTraces}
            toolPhases={toolPhases}
            graphAppendActKinds={graphAppendActKinds}
            graphAppendAuthorizedBy={graphAppendAuthorizedBy}
            prevExecutionIds={prevExecutionIds}
            userInterjections={p.userInterjections}
            turnClosed={!live}
            onFill={onFill}
            supportIds={supportIds}
            onOpenBrowserLive={onOpenBrowserLive}
            finishReason={outcomeNotice ? null : finishReason}
            finishDiagnosisLabel={finishDiagnosis}
            failureNotice={outcomeNotice}
            usage={live ? null : chrome.usage}
            rounds={live ? null : chrome.rounds}
            costText={live ? null : cost}
            durationMs={live ? null : chrome.durationMs}
            clockIso={live ? null : clockIso}
          />
        ) : null}
        <TurnOutcomeBanner
          outcome={outcome}
          supportIds={supportIds}
          onRetry={onRetry}
          onContinue={onContinue}
          continueLocked={continueLocked}
        />
        <FileArtifactsCard
          artifacts={artifacts}
          reviewArtifacts={reviewArtifacts}
          conversationId={conversationId}
          messageId={messageId}
        />
        {/* The team view carries its own progress header; the one-line meta is the
            single-agent fallback. */}
        {!isMulti && meta && <div className="meta">{meta}</div>}
      </div>
    </>
  );
}

// A persisted assistant message, replayed through the SAME fold/rendering as a live turn:
// a multi-agent turn re-folds its run/tool journal (runs.events) into the team view, a
// single-agent tool turn restores its process timeline (runs.process), and the captain's
// reply / 思考 / 引用 come off the authoritative top-level fields. A row with nothing to
// show (a bare tool-only turn) renders nothing.
function HistoryAssistant({
  m,
  conversationId,
  userMessageId,
  onFill,
  onRetry,
  onContinue,
  continueLocked,
  isLast,
}: {
  m: MessageDetail;
  conversationId: string | null;
  userMessageId?: string | null;
  onFill: (text: string) => void;
  onRetry?: () => void;
  onContinue?: () => Promise<void> | void;
  continueLocked?: boolean;
  isLast?: boolean;
}) {
  const {
    team,
    debate,
    debateRounds,
    turnWarning,
    foldEvidenceLedger,
    graphAppendActKinds,
    graphAppendAuthorizedBy,
    prevExecutionIds,
    deliveryStatus,
    userInterjections,
    foldedProcess,
    chrome,
  } = useMemo(() => {
    const events = m.runs?.events;
    const warning =
      m.runs?.turn_warning ??
      (events?.length ? extractTurnWarning(events) : null);
    const emptyChrome = {
      usage: null as UsageBreakdown | null,
      rounds: null as number | null,
      durationMs: null as number | null,
      finishReason: null as string | null,
      emptyDiagnosis: undefined as string | undefined,
      bodyKind: undefined as string | undefined,
      baseUrl: undefined as string | undefined,
      errorCode: undefined as string | undefined,
      errorMessage: undefined as string | undefined,
      credentialSource: undefined as string | null | undefined,
    };
    if (!events || events.length === 0)
      return {
        team: undefined,
        debate: null,
        debateRounds: [] as DebateNarrativeRound[],
        turnWarning: warning,
        foldEvidenceLedger: [],
        graphAppendActKinds: new Map<string, string>(),
        graphAppendAuthorizedBy: new Map<string, string>(),
        prevExecutionIds: new Map<string, string>(),
        deliveryStatus: null,
        userInterjections: [] as ProjectedTurn["userInterjections"],
        foldedProcess: [] as ProjectedTurn["process"],
        chrome: emptyChrome,
      };
    const p = fold(events);
    const team =
      p.runs.length > 0
        ? {
            agents: p.agents,
            runs: p.runs,
            progress: p.progress,
            acts: p.acts,
            teamNotes: p.teamNotes,
            status: p.status,
            conversationId,
            executionId: teamExecutionId(p.process),
            runToolCalls: extractRunToolCalls(events),
            // 辩论场级 `#eN`（勿写入 Message.evidence_ledger 语义）
            evidenceLedger: extractEvidenceLedger(events),
            elapsedMs: turnElapsedMs(events),
            startedAtMs: firstCollabAtMs(events),
            waitProgress: extractCoordinationWait(events),
            detached: extractExecutionDetached(events),
          }
        : undefined;
    return {
      team,
      debate: p.debate,
      debateRounds: p.debateRounds,
      turnWarning: warning ?? p.turnWarning,
      foldEvidenceLedger: p.evidenceLedger,
      graphAppendActKinds: extractGraphAppendActKinds(events),
      graphAppendAuthorizedBy: extractGraphAppendAuthorizedBy(events),
      prevExecutionIds: extractPrevExecutionIds(events),
      deliveryStatus: p.deliveryStatus,
      userInterjections: p.userInterjections,
      foldedProcess: p.process,
      chrome: extractTurnChrome(events),
    };
  }, [m.runs, conversationId]);
  // REST process 权威；旧 journal 未落 user_interjection marker 时用 fold 回放补位。
  const restProcess = m.runs?.process ?? undefined;
  const process = (() => {
    const restHasInj = restProcess?.some((s) => s.kind === "user_interjection");
    const foldHasInj = foldedProcess.some(
      (s) => s.kind === "user_interjection",
    );
    if (foldHasInj && !restHasInj) return foldedProcess;
    return restProcess;
  })();
  // 历史冷启动优先 REST `evidence_ledger`；缺列时回退 journal fold 的回合台账。
  const historyEvidenceLedger = m.evidenceLedger?.length
    ? m.evidenceLedger
    : foldEvidenceLedger;
  // 历史无 events → deliveryStatus 恒 null；有 process 工具产物时旁路出卡 + A1 预览。
  const { list: artifacts, review: reviewArtifacts } = useMemo(
    () =>
      resolveArtifactsForTurn({
        deliveryStatus,
        process,
        events: m.runs?.events,
      }),
    [deliveryStatus, process, m.runs?.events],
  );
  const escalationSlots = useMemo(
    () => extractEscalationSlots(m.runs?.events ?? []),
    [m.runs],
  );
  // 热审批/委派授权痕迹 (D3): 单聊审批回合的 events 也过 journal surface（二期），故历史可取。
  const hotTraces = useMemo(
    () => extractHotDecisionTraces(m.runs?.events ?? []),
    [m.runs],
  );
  const stageCardTraces = useMemo(
    () => extractStageCardTraces(m.runs?.events ?? []),
    [m.runs],
  );
  const teamPreviewTraces = useMemo(
    () => extractTeamPreviewTraces(m.runs?.events ?? []),
    [m.runs],
  );
  // P2：优先用 messages.cost 列（平台记账）；缺列或 BYOK 记账为 0 时 lazy-fetch 台账（含 estimated_cost）。
  const columnBilled =
    m.cost && m.cost.total > 0
      ? {
          nano: m.cost.total,
          estimated: false as const,
          currency: m.cost.currency,
        }
      : null;
  const { ref, money: lazyMoney } = useLazyMessageCost(
    columnBilled == null ? m.id : "",
  );
  const money = columnBilled ?? lazyMoney;
  const cost =
    money && money.nano > 0
      ? formatCost(money.nano, money.estimated, money.currency)
      : m.cost?.pricing_source === "unpriced" || lazyMoney?.unpriced
        ? COST_UNPRICED_LABEL
        : null;
  const streaming = m.status === "running" && !m.paused;
  const finishReason = m.runs?.finish_reason ?? chrome.finishReason ?? null;
  const errorMessage =
    chrome.errorMessage ?? m.runs?.error?.message ?? undefined;
  const errorCode = chrome.errorCode ?? m.runs?.error?.code ?? undefined;
  const emptyBody =
    !team &&
    (!process || process.length === 0) &&
    !m.content &&
    !m.reasoning_content &&
    m.citations.length === 0 &&
    artifacts.length === 0;
  const pauseUi = dedicatedPauseOrAskUi({
    paused: m.paused,
    finishReason,
    process,
  });
  const hasTeamGraph = shouldShowTeamGraph(team?.runs);
  const outcome = resolveTurnOutcomeFromJournal({
    events: m.runs?.events ?? [],
    content: m.content,
    skip: streaming,
    hasDedicatedPauseOrAskUi: pauseUi,
    paused: m.paused,
    finishReason,
    errorCode,
    errorMessage,
    credentialSource: chrome.credentialSource,
    deliveryState: deliveryStatus?.state,
    deliverySummary: deliveryStatus?.summary,
    runs: team?.runs,
    projectedStatus: team?.status,
    wireResult: m.outcome,
    hasTeamGraph,
  });
  const pauseFace = isCeoContinuePause(outcome);
  const outcomeNotice = pauseFace ? null : outcome.notice;
  const supportIds = historySupportIds(
    m,
    conversationId,
    supportErrorExtras({
      errorCode,
      emptyDiagnosis: chrome.emptyDiagnosis,
      bodyKind: chrome.bodyKind,
      baseUrl: chrome.baseUrl,
    }),
    userMessageId,
  );
  const finishDiagnosis = degradedFinishChipLabel(
    chrome.emptyDiagnosis,
    errorMessage,
  );

  if (
    emptyBody &&
    !turnWarning &&
    !streaming &&
    !outcomeNotice &&
    !pauseFace &&
    userInterjections.length === 0
  ) {
    return null;
  }
  return (
    <>
      <div
        className="bubble assistant"
        ref={columnBilled == null ? ref : undefined}
      >
        {m.recovered && <RecoveredChip />}
        {turnWarning && <div className="turn-warning">{turnWarning}</div>}
        {streaming && !m.content && !m.reasoning_content && !process?.length ? (
          <span className="muted">…</span>
        ) : emptyBody && outcomeNotice ? null : (
          <AssistantContent
            process={process}
            content={m.content ?? ""}
            reasoning={m.reasoning_content ?? undefined}
            citations={m.citations}
            evidenceLedger={historyEvidenceLedger}
            isStreaming={streaming}
            messageId={m.id}
            captainContext={m.runs?.captain_context ?? undefined}
            team={
              team
                ? {
                    ...team,
                    outcome,
                    supportIds,
                    onRetry: isLast ? onRetry : undefined,
                  }
                : undefined
            }
            debate={debate}
            debateRounds={debateRounds}
            escalationSlots={escalationSlots}
            hotTraces={hotTraces}
            stageCardTraces={stageCardTraces}
            teamPreviewTraces={teamPreviewTraces}
            graphAppendActKinds={graphAppendActKinds}
            graphAppendAuthorizedBy={graphAppendAuthorizedBy}
            prevExecutionIds={prevExecutionIds}
            userInterjections={userInterjections}
            turnClosed
            onFill={onFill}
            supportIds={supportIds}
            finishReason={streaming || outcomeNotice ? null : finishReason}
            finishDiagnosisLabel={finishDiagnosis}
            failureNotice={outcomeNotice}
            usage={streaming ? null : (m.usage ?? chrome.usage)}
            rounds={streaming ? null : (m.rounds ?? chrome.rounds)}
            costText={streaming ? null : cost}
            durationMs={streaming ? null : (m.duration_ms ?? chrome.durationMs)}
            clockIso={streaming ? null : m.created_at}
          />
        )}
        <TurnOutcomeBanner
          outcome={outcome}
          supportIds={supportIds}
          onRetry={isLast ? onRetry : undefined}
          onContinue={onContinue}
          continueLocked={continueLocked}
        />
        <FileArtifactsCard
          artifacts={artifacts}
          reviewArtifacts={reviewArtifacts}
          conversationId={conversationId}
          messageId={m.id}
        />
      </div>
    </>
  );
}

export function ChatPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { id: conversationId } = useParams<{ id: string }>();
  // 历史对话抽屉 (☰): the chat is the landing surface now; history slides in over it.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [history, setHistory] = useState<MessageDetail[] | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState(() =>
    conversationId ? "" : (pendingEmptyRollback?.text ?? ""),
  );
  const [sending, setSending] = useState(false);
  // 创建会话在途时手里捧着的那份草稿：按下发送就收走输入框并把它摆成气泡（不等 POST 回来），
  // 失败时整份还回输入框。
  const [draftPending, setDraftPending] = useState<{
    text: string;
    attachments: MessageAttachment[];
    agentMentions: PendingAgentMention[];
  } | null>(null);
  /** 草稿选中的已有云文件夹；null = 快速对话。抽屉「在此新开」经 location.state 预填。 */
  const [draftFolder, setDraftFolder] = useState<DraftFolder | null>(() =>
    conversationId ? null : (pendingEmptyRollback?.folder ?? null),
  );
  /** 已开对话所属文件夹名；null = 裸聊 / 尚未解析 → 顶栏「本对话文件」。 */
  const [workspaceLabel, setWorkspaceLabel] = useState<string | null>(null);
  /** 诚实停止过渡：stopping 时 UI 不先于后端进终态；与 sending 合成 busy。 */
  const [stopPhase, setStopPhase] = useState<StopUiPhase>("idle");
  const [error, setError] = useState<ChatError | null>(() =>
    conversationId ? null : (pendingEmptyRollback?.error ?? null),
  );
  /** 对账发现本地幽灵项（服务端重启丢队）时的一次轻提示。 */
  const [queueDroppedHint, setQueueDroppedHint] = useState<string | null>(null);
  /** 本会话权限四轴（草稿本地；已有会话跟 conversation.permission_axes）。 */
  const [permissionAxes, setPermissionAxes] = useState<PermissionAxes>(
    DEFAULT_PERMISSION_AXES,
  );
  const [permissionDraftTouched, setPermissionDraftTouched] = useState(false);
  // 会话级模型组合 (定案 B · 拍快照): snapshotted profile id (null = draft / not yet chosen).
  // A draft seeds from last-used profile；「＋」菜单打开 ModelPicker（只选具体组合）。
  const [currentProfileId, setCurrentProfileId] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [permissionSheetOpen, setPermissionSheetOpen] = useState(false);
  const { data: modelProfiles } = useModelProfiles();
  // Files staged for the next send (composer 附件): text inline and/or binary resident.
  // Oversized / upload failures surface `attachError` and aren't staged.
  const [attachments, setAttachments] = useState<MessageAttachment[]>(() =>
    conversationId ? [] : (pendingEmptyRollback?.attachments ?? []),
  );
  const [agentMentions, setAgentMentions] = useState<PendingAgentMention[]>(
    () => (conversationId ? [] : (pendingEmptyRollback?.agentMentions ?? [])),
  );
  const [attachError, setAttachError] = useState<string | null>(null);
  const attachInputRef = useRef<HTMLInputElement>(null);
  // The composer textarea — focused after ask / debate handoff fill so the user can edit/send.
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const mention = useComposerMention({
    conversationId: conversationId ?? null,
    input,
    setInput,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    history: history ?? [],
    turns,
    textareaRef: composerInputRef,
    onPickAttach: () => attachInputRef.current?.click(),
    onError: setAttachError,
  });
  // Turns that paused at a checkpoint then lost their stream (durable resume frames),
  // recovery shell on reopen (结构化挂起 2b). Live paint authority = cold Interaction store.
  const [paused, setPaused] = useState<PausedTurnSummary[]>([]);
  const coldById = useColdInteractions();
  /** CEO host server message_id for the active live turn (message_start stamp). */
  const hostServerMessageIdRef = useRef<string | null>(null);
  /** Sandbox browser live sheet (Step4 · C): login card / hot escalate open this. */
  const [browserLiveOpen, setBrowserLiveOpen] = useState(false);
  const [browserLiveSessionId, setBrowserLiveSessionId] = useState<
    string | null
  >(null);
  const openBrowserLive = useCallback(
    (opts?: OpenBrowserLiveOpts) => {
      if (!conversationId) {
        setBrowserLiveOpen(true);
        return;
      }
      void listBrowserSessions(conversationId)
        .then((list) => {
          let sid = "";
          const wantRun = opts?.runId?.trim();
          if (wantRun) {
            const match = list.sessions.find(
              (s) => s.runId?.trim() === wantRun,
            );
            if (match?.sessionId?.trim()) sid = match.sessionId.trim();
          }
          if (!sid) {
            sid =
              list.activeSessionId?.trim() ||
              list.sessions[0]?.sessionId?.trim() ||
              "";
          }
          setBrowserLiveSessionId(sid || null);
        })
        .catch(() => {
          setBrowserLiveSessionId(null);
        })
        .finally(() => {
          setBrowserLiveOpen(true);
        });
    },
    [conversationId],
  );
  const [recoveredInteractions, setRecoveredInteractions] = useState<
    PendingInteractionSummary[]
  >([]);
  // Older messages exist above the loaded window (drives 加载更早); `loadingOlder` blocks
  // re-entrancy while a page is in flight (历史上翻分页).
  const [hasMoreBefore, setHasMoreBefore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // 「记忆已更新」卡 (③ §1.6): the latest window's offline-consolidation results, anchored back
  // into the thread by `anchorAt` (memoryAnchors). Only the newest window carries them, so
  // scroll-up (loadOlder) leaves them be.
  const [memoryUpdates, setMemoryUpdates] = useState<MemoryUpdate[]>([]);
  // Offline consolidation post-dates the turn — bump this gen to cancel in-flight polls on
  // conversation switch / unmount (mobile has no memory_updated firehose).
  const memoryPollGenRef = useRef(0);
  // REL-002: execution_completed → short-delay getMessages retries for harvest 终稿.
  // Cancel on conversation switch (leave/reopen still loads via the effect below).
  const harvestRefreshRef = useRef(
    createHarvestRefreshScheduler(async (cid, isCurrent) => {
      const {
        messages,
        hasMoreBefore: more,
        memoryUpdates: mem,
      } = await getMessages(cid);
      if (!isCurrent()) return;
      setHistory(messages);
      setHasMoreBefore(more);
      setMemoryUpdates(mem);
      setTurns((t) => dropSettledLiveTurns(t));
    }),
  );
  // The controller for the stream currently held open (send / reattach). Conversation
  // switch aborts it; 用户停止 does NOT — keep SSE until backend message_end (诚实过渡).
  const abortRef = useRef<AbortController | null>(null);
  /** 当前会话 id 的同步读（跨 await 判身份；state 闭包会陈旧）。 */
  const conversationIdRef = useRef<string | undefined>(conversationId);
  conversationIdRef.current = conversationId;
  /** 历史窗同步读：pendingFirstSend 的 send 闭包里 history 还是 null。 */
  const historyRef = useRef(history);
  historyRef.current = history;
  /** 对话级订阅（`follow`）的连接槽——与本端自发流互斥，见 {@link claimLocalStream}。 */
  const followRef = useRef<AbortController | null>(null);
  /** 该订阅正在跟播的回合；`message_id` 变 = 另一端起了新回合，另开气泡。 */
  const followTurnRef = useRef<FollowTurnCursor | null>(null);
  /** 下一条订阅的首个回合段该认领的既有气泡（断线重连 / 重开续看），而不是新开一个。 */
  const followAdoptRef = useRef<string | null>(null);
  /**
   * 这条订阅是**重连**挂上的（手点重连 / 回前台 / 服务端关流后重挂）。断线期间另一端整跑完
   * 的回合不会被重放（服务端只重放仍在跑的 run），所以连上确认空闲后要补一次消息窗对账。
   */
  const followReconnectedRef = useRef(false);
  /** 挂订阅时本端认为「有回合在跑」——空闲信号即证伪：撤空转气泡 + 回读终稿。 */
  const expectLiveRunRef = useRef(false);
  /** 重挂订阅的代号（回前台 / 手动重连 / 本端自发流让位后归位）。 */
  const [followEpoch, setFollowEpoch] = useState(0);
  /** 打开会话的定面流程（历史 + 恢复快照）是否已完成——定面前不挂订阅，免与 clear-then-fold 抢。 */
  const [followReady, setFollowReady] = useState(false);
  /** 订阅上有回合在跟播（本端自发流用 `sending`，两者互斥，一起合成 busy）。 */
  const [followRunning, setFollowRunning] = useState(false);
  /** Mid-flight AC 集合（切会话一并 abort；Stop 不清队、不 abort 排队连接）。 */
  const midFlightControllersRef = useRef(new Set<AbortController>());
  /** queue_id → mid-flight AC（取消成功后再 abort，避免失败留下断连坏态）。 */
  const midFlightByQueueRef = useRef(new Map<string, AbortController>());
  /**
   * 排队条对账（GET 权威）。ref 避免 appendEventToTurn / reconnect 闭包陈旧。
   * 本地有项而服务端已无 → 一次轻提示再清。
   */
  /**
   * 此刻真摆在用户面前的卡（interaction id）。收口事件不带处理方，所以「另一端处理了」的
   * 判据是「本端正看着这张卡 + 本端没点过」；渲染时刷新（见下方卡片选择器）。
   */
  const visibleCardIdsRef = useRef<ReadonlySet<string>>(new Set<string>());
  const reconcileQueuedRef = useRef<(cid: string) => void>(() => {});
  reconcileQueuedRef.current = (cid: string) => {
    void reconcileQueuedTurns(cid).then((result) => {
      if (result.failed) return;
      if (result.droppedLocalIds.length > 0) {
        setQueueDroppedHint(QUEUE_DROPPED_HINT);
      }
    });
  };
  /** 当前主路 / 续流写入目标 turn id（排队期条外仍指向主路，勿写到队尾）。 */
  const activeTurnIdRef = useRef<string | null>(null);
  const [activeStreamTurnId, setActiveStreamTurnId] = useState<string | null>(
    null,
  );
  const setActiveTurn = (id: string | null) => {
    if (id !== activeTurnIdRef.current) {
      hostServerMessageIdRef.current = null;
    }
    activeTurnIdRef.current = id;
    setActiveStreamTurnId(id);
  };
  /** 主路 SSE 是否仍在泵（供 mid-flight 等 drain 边界）。 */
  const primaryActiveRef = useRef(false);
  const primaryIdleWaitersRef = useRef<Array<() => void>>([]);
  /** 主路 + mid-flight 在途数；>0 则 sending。 */
  const inflightRef = useRef(0);
  const stopPhaseRef = useRef<StopUiPhase>("idle");
  /**
   * 「正在建会话」的同步栓。不能用 `busy`（`sending` 是 React 派生态，`setSending` 要到下一帧
   * 才生效）：同一帧里的两次提交（双击 / Enter 连按 / 触摸+点击双触发）会一起穿过闸门各建一条
   * 会话——线上 7 天 8 起重复会话就是这么来的（最短间隔 14ms）。栓在第一个 await 之前置位。
   */
  const creatingConversationRef = useRef(false);
  /**
   * 建会话的幂等键，生命周期跟着这份草稿走：同一份草稿重发（失败重试）复用同一把，服务端同键
   * 只会给回同一条会话；发送成功或用户把草稿清空后轮换。
   */
  const draftRequestIdRef = useRef<string | null>(null);

  const markStreamStart = () => {
    inflightRef.current += 1;
    setSending(true);
  };

  const markStreamEnd = () => {
    inflightRef.current = Math.max(0, inflightRef.current - 1);
    if (inflightRef.current === 0) setSending(false);
  };

  const signalPrimaryIdle = () => {
    primaryActiveRef.current = false;
    const waiters = primaryIdleWaitersRef.current.splice(0);
    for (const w of waiters) w();
  };

  const waitPrimaryIdle = () => {
    if (!primaryActiveRef.current) return Promise.resolve();
    return new Promise<void>((resolve) => {
      primaryIdleWaitersRef.current.push(resolve);
    });
  };

  const applyStopPhase = (phase: StopUiPhase) => {
    stopPhaseRef.current = phase;
    setStopPhase(phase);
  };

  /** Fresh read — avoids TS narrowing `stopPhaseRef.current` across awaits. */
  const isStoppingNow = (): boolean => stopPhaseRef.current === "stopping";

  const clearStopping = () => {
    applyStopPhase("idle");
  };

  // 本端自发流（sending）与跟播另一端的回合（followRunning）都算「回合在跑」，二者互斥。
  // 两者必须同权：另一端的回合在跑时再发，同样要走 mid-flight（排队 / 插话），不能当空闲开跑。
  const turnInFlight = sending || followRunning;
  const busy = isStopBusy(turnInFlight, stopPhase);

  /**
   * 认领主时间线：本端自发流（send / 出队开跑 / resume / 推进卡 / 重试）独占折叠权。
   * 同步掐掉对话级订阅——它跟的是同一条对话，不让位就会把同一个回合再折一遍（双气泡）。
   * 本端流收尾时经 {@link releaseLocalStream} 归还槽位，订阅随即自动归位。
   */
  const claimLocalStream = (ac: AbortController) => {
    followRef.current?.abort();
    followRef.current = null;
    followTurnRef.current = null;
    followAdoptRef.current = null;
    abortRef.current = ac;
  };

  /**
   * 本端自发流让出主时间线 → 订阅归位。返回是否真的由本流持有（沿用既有身份守卫语义：
   * 被接管的陈旧流不许收尾）。必须显式发信号，不能只看 `sending`——冷卡可操作时它会提前落回
   * false 解锁 composer，那时流还开着，订阅一挂上就会和它折同一个回合。
   */
  const releaseLocalStream = (ac: AbortController) => {
    if (abortRef.current !== ac) return false;
    abortRef.current = null;
    setFollowEpoch((e) => e + 1);
    return true;
  };

  /** 切换会话：订阅跟着对话走，连同它的回合游标与 busy 一起归零。 */
  const resetFollowState = () => {
    followRef.current?.abort();
    followRef.current = null;
    followTurnRef.current = null;
    followAdoptRef.current = null;
    followReconnectedRef.current = false;
    expectLiveRunRef.current = false;
    abortRef.current = null;
    setFollowRunning(false);
    setFollowReady(false);
  };

  // Stick-to-bottom with upward-gesture detach + hysteresis (流式时上滑不强制贴底).
  // contentKey grows with the live tail so streaming tokens re-pin only while stuck.
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;
  const lastHist =
    history && history.length > 0 ? history[history.length - 1] : null;
  const scrollContentKey = `${history?.length ?? 0}-${turns.length}-${lastTurn?.id ?? ""}-${lastTurn?.events.length ?? 0}-${lastHist?.id ?? ""}-${lastHist?.content?.length ?? 0}`;
  const { scrollRef, atBottom, jumpToBottom, preparePrepend, cancelPrepend } =
    useStickScroll(scrollContentKey, conversationId ?? null);

  // 语音输入 (桌面对齐)：转写文本追加到现有草稿 (不覆盖)，完成后聚焦输入框供编辑再发。
  // web 浏览器走 Web Speech API、原生壳走 capgo 插件，两者都不可用则 isSupported=false (按钮隐藏)。
  const voice = useVoiceInput({
    onTranscript: useCallback((text: string) => {
      setInput((prev) => (prev.trim() ? `${prev} ${text}` : text));
      requestAnimationFrame(() => composerInputRef.current?.focus());
    }, []),
  });

  /**
   * 收口事件的归属判定（B2 · P1 · 验收 5）：本端登记过 = 自己点的，静默收卡；另一端点的
   * 留一张「已由另一端处理」——卡凭空消失会让用户以为是自己刚点的。
   */
  const noteForeignSettlement = (event: SSEEvent) => {
    if (!conversationId) return;
    const settled = settlementFromResolvedEvent(
      event.type,
      event.payload as Record<string, unknown> | undefined,
    );
    if (!settled) return;
    if (
      !isForeignSettlement(settled.interactionId, visibleCardIdsRef.current)
    ) {
      return;
    }
    noteRemoteSettlement({
      interactionId: settled.interactionId,
      conversationId,
      kind: settled.kind,
    });
  };

  // Append an event to a specific turn (主路 / mid-flight 续流各写各的；勿依赖「最后一项」).
  // Lazily opens a userText-less turn when none exists yet (reattach on reopen).
  const appendEventToTurn = (turnId: string | null, event: SSEEvent) => {
    if (conversationId) noteConversationStreamEvent(conversationId, event);
    // 诚实停止：stopping 丢弃正文/工具突变，仍消费 run_* + 终态确认。
    if (
      stopPhaseRef.current === "stopping" &&
      !allowsEventWhileStopping(event.type)
    ) {
      return;
    }
    noteForeignSettlement(event);
    // EPHEMERAL：冷 resume × live deferred — 同连接等待槽空，不是 409。
    if (event.type === "resume_deferred") {
      const p = event.payload as ResumeDeferredPayload;
      if (
        p.message_id &&
        (p.busy_reason === "wrap_up" || p.busy_reason === "live_turn")
      ) {
        const cid = p.conversation_id || conversationId || "";
        // 另一端放行的冷卡：settlement 已锁，这端再点也没有意义。先收成「已由另一端
        // 处理」——留着「放行已记下…」会让用户以为是自己刚点的。
        if (cid) {
          for (const entry of listColdPending(cid)) {
            if (entry.messageId !== p.message_id) continue;
            if (!isForeignSettlement(entry.id, visibleCardIdsRef.current)) {
              continue;
            }
            noteRemoteSettlement({
              interactionId: entry.id,
              conversationId: cid,
              kind: entry.kind,
            });
            markColdResolved({ kind: entry.kind, id: entry.id });
          }
        }
        // 跟播 / 本端：settlement 已锁，recovery 壳不能再画可点卡。
        setPaused((prev) => prev.filter((x) => x.message_id !== p.message_id));
        // 本端自己发起的那张仍走「放行已记下…」（上面已收口的不再是 pending，自动跳过）。
        markColdDeferred({
          messageId: p.message_id,
          conversationId: cid || undefined,
          busyReason: p.busy_reason,
        });
      }
      return;
    }
    // EPHEMERAL：按项取消 → 只清条 + abort mid-flight（排队期无主时间线用户泡）。
    if (event.type === "turn_queue_cancelled" && conversationId) {
      const p = event.payload as TurnQueueCancelledPayload;
      removeQueuedTurn(conversationId, p.queue_id);
      const ac = midFlightByQueueRef.current.get(p.queue_id);
      if (ac) {
        ac.abort();
        midFlightByQueueRef.current.delete(p.queue_id);
        midFlightControllersRef.current.delete(ac);
      }
      // 队列变动的权威是 GET：另一端取消掉一项后，余下各项的序号 / 深度都变了。
      reconcileQueuedRef.current(conversationId);
      return;
    }
    // EPHEMERAL：出队开跑（sink 首帧，先于 message_start）→ 清条；用户泡由 beginTurn2 插入。
    // 否决靠 message_start 猜出队。
    if (event.type === "turn_queue_started" && conversationId) {
      const p = event.payload as TurnQueueStartedPayload;
      removeQueuedTurn(conversationId, p.queue_id);
      // 已开跑：不再可按项取消；勿 abort（同连接续流 turn2）。
      midFlightByQueueRef.current.delete(p.queue_id);
      // 出队同样挪动余下各项的序号 → 拉一次权威快照（禁轮询）。
      reconcileQueuedRef.current(conversationId);
      return;
    }
    // turn_queued：发送路径已由 onQueued 本地即时写入；此处仅当信号缺本地项时对账
    // （协调升格进队 / 多端同步等）。不写入主路 events。
    if (event.type === "turn_queued") {
      if (conversationId) {
        const p = event.payload as TurnQueuedPayload;
        const queueId = typeof p.queue_id === "string" ? p.queue_id : "";
        if (
          queueId &&
          !listQueuedTurns(conversationId).some((e) => e.queueId === queueId)
        ) {
          reconcileQueuedRef.current(conversationId);
        }
      }
      return;
    }
    let createdId: string | null = null;
    setTurns((t) => {
      if (t.length === 0) {
        const id = turnId ?? crypto.randomUUID();
        createdId = id;
        return [{ id, userText: null, events: [event] }];
      }
      const targetId = turnId ?? activeTurnIdRef.current ?? t[t.length - 1].id;
      const idx = t.findIndex((x) => x.id === targetId);
      if (idx < 0) {
        // 目标不存在（已取消）——丢弃，勿污染其它 turn。
        return t;
      }
      const next = t.slice();
      const cur = next[idx];
      next[idx] = { ...cur, events: [...cur.events, event] };
      return next;
    });
    if (createdId && !activeTurnIdRef.current) setActiveTurn(createdId);
    if (stopPhaseRef.current === "stopping" && isStopConfirmEvent(event.type)) {
      clearStopping();
    }
    // Live cold ResumeCard authority (检查点与开工卡 · Live 出卡):
    // `*_required` → cold IX; message_start stamp → rekey/bind → paint. Do not wait for
    // message_end → getRecovery as the only path (desktop parity; mobile-local store).
    if (conversationId) {
      if (event.type === "message_start") {
        const serverId = (event.payload as MessageStartPayload).message_id;
        const clientId = turnId ?? activeTurnIdRef.current ?? createdId ?? null;
        if (serverId) {
          hostServerMessageIdRef.current = serverId;
          if (clientId) rekeyColdMessageId(clientId, serverId);
          bindEmptyColdMessageId(conversationId, serverId);
          // Deferred wait ends when claim+续跑 stamps message_start on this host.
          for (const e of listColdPending(conversationId)) {
            if (e.messageId !== serverId) continue;
            if (e.status !== "submitting") continue;
            markColdResolved({
              kind: e.kind,
              id: e.id,
              resolution: e.resolution,
            });
          }
        }
      }
      const isColdWire =
        kindFromColdRequiredEvent(event.type) != null ||
        kindFromColdResolvedEvent(event.type) != null ||
        event.type === "interaction_orphaned";
      if (isColdWire) {
        // Cold bind: never nail pending to an unsealed client bubble when a
        // same-turn stamp / resume key exists (ask continue → team_preview).
        const preferred =
          hostServerMessageIdRef.current ??
          turnId ??
          activeTurnIdRef.current ??
          createdId ??
          "";
        const bindHosts: ColdResumeHost[] = [];
        for (const m of history ?? []) {
          if (m.role === "assistant") {
            bindHosts.push({
              role: "assistant",
              id: m.id,
              serverMessageId: m.id,
            });
          }
        }
        const targetTurnId =
          turnId ?? activeTurnIdRef.current ?? createdId ?? null;
        for (const t of turns) {
          const events =
            targetTurnId && t.id === targetTurnId
              ? [...t.events, event]
              : t.events;
          bindHosts.push({
            role: "assistant",
            id: t.id,
            serverMessageId: extractMessageId(events),
          });
        }
        if (createdId && !turns.some((t) => t.id === createdId)) {
          bindHosts.push({
            role: "assistant",
            id: createdId,
            serverMessageId: extractMessageId([event]),
          });
        }
        const hostId = resolveColdBindHostId(bindHosts, preferred, {
          resumeStamp: hostServerMessageIdRef.current,
        });
        applyColdInteractionWireEvent(
          event.type,
          (event.payload ?? {}) as Record<string, unknown>,
          conversationId,
          hostId,
        );
        const resolvedKind = kindFromColdResolvedEvent(event.type);
        if (resolvedKind) {
          const checkpointId = idFromColdRequiredPayload(
            resolvedKind,
            (event.payload ?? {}) as Record<string, unknown>,
          );
          if (checkpointId) {
            setPaused((prev) =>
              prev.filter((x) => x.checkpoint_id !== checkpointId),
            );
          }
        }
      }
    }
    // 挂起即收口 (②): a live stream can END at a durable checkpoint — message_end carries
    // finish_reason=paused. The turn finalized (its in-process resolve Future was never
    // parked), so the live PauseCard no longer applies; re-read the recovery snapshot so
    // its durable ResumeCard surfaces once the stream settles (the single cold resume
    // path), exactly as a reopen would. One chokepoint for every stream
    // (send/resume/reconnect/attach), mirroring the desktop's message_end handler.
    if (conversationId && event.type === "message_end") {
      if ((event.payload as MessageEndPayload).finish_reason === "paused") {
        void refreshPaused(conversationId);
      }
      // 记忆更新可发现性: consolidation is offline/async — schedule delayed refreshes of
      // the thread-tail card so the user does not have to leave and reopen the chat.
      scheduleMemoryPoll(conversationId);
    }
    // REL-002: 后台执行终态 — fold no-op；经 getMessages 拉入 harvest 终稿（短延迟重试）。
    if (conversationId && event.type === "execution_completed") {
      harvestRefreshRef.current.schedule(conversationId);
    }
  };

  // Pull the latest window's memory_updates into the thread-tail card. Best-effort; a
  // failure must never disrupt the settled turn.
  async function refreshMemoryUpdates(cid: string) {
    try {
      const { memoryUpdates: next } = await getMessages(cid);
      setMemoryUpdates(next);
    } catch {
      /* ignore — poll is best-effort */
    }
  }

  // Delayed poll after message_end (2s / 8s / 20s). Always runs the full schedule so a
  // pre-existing card from an earlier turn does not abort before a fresh pass lands;
  // cancelled when the conversation changes (gen bump).
  function scheduleMemoryPoll(cid: string) {
    const gen = ++memoryPollGenRef.current;
    const delays = [2000, 8000, 20000];
    void (async () => {
      for (const ms of delays) {
        await new Promise((r) => setTimeout(r, ms));
        if (memoryPollGenRef.current !== gen) return;
        await refreshMemoryUpdates(cid);
        if (memoryPollGenRef.current !== gen) return;
      }
    })();
  }

  // 抽屉「在此新开」经 location.state 预填草稿文件夹。空 state 不要冲掉回滚还回的 folder。
  useEffect(() => {
    if (conversationId) return;
    const fromNav = readDraftFolderState(location.state);
    if (fromNav) setDraftFolder(fromNav);
  }, [conversationId, location.state]);

  // Load the persisted transcript for the conversation in the URL — this is what makes a
  // refresh keep the conversation (刷新不丢): the id rides the route, the history is the
  // server's. Turns sent this session stream live below it (via the fold). If the latest
  // turn has no persisted reply (ends at a user message), a run may still be live
  // (执行与请求解耦 C1 · slice 1b): rejoin it and 续看 it finish.
  // biome-ignore lint/correctness/useExhaustiveDependencies: effect 按会话(conversationId/navigate)生命周期挂载重连 run；clearStopping 等仅作副作用调用，列入依赖会破坏重挂载语义
  useEffect(() => {
    // Cancel any in-flight memory-update polls / harvest refreshes from the previous conversation.
    memoryPollGenRef.current += 1;
    harvestRefreshRef.current.cancel();
    if (!conversationId) {
      // Draft (直接对话): no server conversation yet — ready to type, nothing to load.
      let draftCancelled = false;
      const handed = pendingEmptyRollback;
      setHistory([]);
      setTurns([]);
      if (handed) {
        setError(handed.error);
        setInput(handed.text);
        setAttachments(handed.attachments);
        setAgentMentions(handed.agentMentions);
        setDraftFolder(handed.folder);
      } else {
        setError(null);
      }
      queueMicrotask(() => {
        if (draftCancelled) return;
        if (handed && pendingEmptyRollback === handed) {
          pendingEmptyRollback = null;
        }
      });
      setQueueDroppedHint(null);
      setSending(false);
      // 全新的草稿面：栓、在途草稿、幂等键一起归零（上一份草稿已经落成会话或被丢弃）。
      // 拆空会话回来时也不要钉回旧键——startDraft 成功时已经置 null。
      creatingConversationRef.current = false;
      draftRequestIdRef.current = null;
      setDraftPending(null);
      resetFollowState();
      clearStopping();
      setPaused([]);
      clearColdInteractions();
      resetRemoteSettlements();
      hostServerMessageIdRef.current = null;
      setHasMoreBefore(false);
      setMemoryUpdates([]);
      setPermissionDraftTouched(false);
      setMoreOpen(false);
      setPermissionSheetOpen(false);
      // 新对话继承上次选择: seed the draft's profile from last-used (localStorage);
      // passed as POST model_profile_id on first send (startDraft · 定案 B).
      setCurrentProfileId(getLastModelProfileId());
      setActiveTurn(null);
      setWorkspaceLabel(null);
      // Seed draft axes from account default recipe (best-effort).
      void getAutonomy()
        .then((d) => {
          setPermissionAxes(recipeToAxes(d.policy));
        })
        .catch(() => {
          setPermissionAxes(DEFAULT_PERMISSION_AXES);
        });
      return () => {
        draftCancelled = true;
      };
    }
    setHistory(null);
    setTurns([]);
    pendingEmptyRollback = null;
    setError(null);
    setQueueDroppedHint(null);
    setSending(false);
    // 会话已经落地：在途草稿交棒给 pendingFirstSend 的首发气泡。
    creatingConversationRef.current = false;
    setDraftPending(null);
    resetFollowState();
    setPaused([]);
    clearColdInteractions();
    resetRemoteSettlements();
    // 人已经到场：撤掉「这个对话在等你」的全局提示条（也兜住断线期间漏收的 resolved）。
    clearAiAttentionForConversation(conversationId);
    hostServerMessageIdRef.current = null;
    setRecoveredInteractions([]);
    setBrowserLiveOpen(false);
    setBrowserLiveSessionId(null);
    setHasMoreBefore(false);
    setMemoryUpdates([]);
    setPermissionAxes(DEFAULT_PERMISSION_AXES);
    setPermissionDraftTouched(false);
    setMoreOpen(false);
    setPermissionSheetOpen(false);
    setCurrentProfileId(null);
    setActiveTurn(null);
    setWorkspaceLabel(null);
    let cancelled = false;
    void getConversation(conversationId)
      .then(async (c) => {
        if (cancelled) return;
        setPermissionAxes(normalizeAxes(c.permission_axes));
        setCurrentProfileId(c.model_profile_id ?? null);
        if (!c.folder_id) {
          setWorkspaceLabel(null);
          return;
        }
        try {
          const folder = await getFolder(c.folder_id);
          if (!cancelled) setWorkspaceLabel(folder.name);
        } catch {
          if (!cancelled) setWorkspaceLabel(null);
        }
      })
      .catch(() => {
        /* best-effort */
      });
    // 统一恢复态快照（recovery 统一, 对称 §18.2）：一次属主校验读，既给出「待恢复」卡要用的挂起帧
    // （结构化挂起 2b），又给出「是否还有 detached live run 可续看」(slice 1b)。尽力而为，永不阻塞
    // 打开会话（失败 = 空快照，回合下次重开仍可恢复）。保留为 promise，让下方 attach 决策对齐同一
    // 份快照（与桌面端一致）。
    const recoveryLoaded = getRecovery(conversationId).catch(
      (): TurnRecovery => ({
        liveRunning: false,
        paused: [],
        pendingInteractions: [],
      }),
    );
    // 排队条对账：开会话 / 切会话拉 GET 权威快照（禁轮询；EPHEMERAL 仅信号）。
    reconcileQueuedRef.current(conversationId);
    void recoveryLoaded.then((r) => {
      if (!cancelled) {
        setPaused(r.paused);
        setRecoveredInteractions(r.pendingInteractions);
        // Hydrate cold IX from recovery paused frames (reopen shell → live authority).
        for (const p of r.paused) {
          if (!isColdResumeKind(p.kind)) continue;
          upsertColdRequired({
            kind: p.kind,
            conversationId,
            messageId: p.message_id,
            payload: pausedSummaryToRequiredPayload(p),
            status: "pending",
          });
        }
      }
    });
    getMessages(conversationId)
      .then(async ({ messages, hasMoreBefore: more, memoryUpdates }) => {
        if (cancelled) return;
        setHistory(messages);
        historyRef.current = messages;
        setHasMoreBefore(more);
        // 「记忆已更新」卡 (③ §1.6): only the latest window carries them — anchor them back into
        // the thread. A (re)open/refresh loads them; after message_end we also poll (no
        // firehose), and scroll-up (loadOlder) never overwrites them.
        setMemoryUpdates(memoryUpdates);
        // A draft's first message, handed off across the / → /c/:id remount: POST + stream
        // it now (the conversation exists but has no run yet, so attach would no-op).
        if (pendingFirstSend && pendingFirstSend.id === conversationId) {
          const p = pendingFirstSend;
          pendingFirstSend = null;
          // 定面完成：这一发自己就是本端自发流，订阅等它跑完再归位。
          setFollowReady(true);
          void send({
            text: p.text,
            attachments: p.attachments,
            agentMentions: p.agentMentions,
            folder: p.folder,
          });
          return;
        }
        const last = messages[messages.length - 1];
        if (last && last.role === "user") {
          // 单一快照决定唯一可操作面：仅当有 detached live run 且无挂起帧时才摆出「续看中」。挂起即
          // 收口 (②)：到达 checkpoint 的回合已 FINALIZE（run 结束、落帧），是纯 durable——「待恢复」
          // 卡为唯一面，不再续看（唯一的 live∩durable 重叠是 §六-1 薄网，帧没存住、paused 本就为
          // 空，进不到这支）。liveRunning 与订阅端点同源活性判据 → 一次读即定面，liveRunning/
          // paused 不会互相矛盾（源头消除竞态，而非排序绕过）。
          // 订阅本身无条件挂（下方 effect）：空闲对话也要停在上面等另一端起回合。
          const recovery = await recoveryLoaded;
          if (cancelled) return;
          if (recovery.liveRunning && recovery.paused.length === 0) {
            expectLiveRunRef.current = true;
            setFollowRunning(true);
          }
        } else if (
          last &&
          last.role === "assistant" &&
          last.status === "running"
        ) {
          // P4: overlay partial already painted; live → clear-then-fold rejoin;
          // dead lease ghost → interrupted affordance (no forever spinner).
          const recovery = await recoveryLoaded;
          if (cancelled) return;
          if (recovery.liveRunning && recovery.paused.length === 0) {
            // clear-then-fold：丢掉 running 影子行，订阅的重放段会把整段重建出来。
            setHistory((h) => (h ? dropRunningAssistantTail(h) : h));
            setTurns([]);
            expectLiveRunRef.current = true;
            setFollowRunning(true);
          } else if (!recovery.liveRunning && recovery.paused.length === 0) {
            // pause 闩（usage.paused / finish_reason=paused）是挂起回合，不是死租约。
            // hold：禁止 stamp incomplete/interrupted，提问卡 / ResumeCard 才是该面。
            const pauseLatch =
              Boolean(last.paused) || last.runs?.finish_reason === "paused";
            if (!pauseLatch) {
              setHistory((h) => {
                if (!h || h.length === 0) return h;
                const next = h.slice();
                const i = next.length - 1;
                next[i] = {
                  ...next[i],
                  status: "incomplete",
                  runs: {
                    events: next[i].runs?.events ?? [],
                    finish_reason: "interrupted",
                    process: next[i].runs?.process ?? null,
                    captain_context: next[i].runs?.captain_context,
                    turn_warning: next[i].runs?.turn_warning,
                  },
                };
                return next;
              });
            }
          }
        }
        // 定面完成 → 挂对话级订阅（无论是否有回合在跑：空闲对话也要停在上面等）。
        await recoveryLoaded;
        if (cancelled) return;
        setFollowReady(true);
      })
      .catch((e) => {
        if (cancelled) return;
        if (!getTokens()) {
          navigate("/login", { replace: true });
          return;
        }
        setError({ text: e instanceof Error ? e.message : "加载消息失败" });
        setHistory([]);
      });
    // Switching conversation aborts any held stream so its events can't pollute the next
    // conversation's turns (shared state — turns aren't keyed by conversation). The server
    // run keeps going detached (slice 1a); reopening reattaches.
    return () => {
      cancelled = true;
      harvestRefreshRef.current.cancel();
      abortRef.current?.abort();
      followRef.current?.abort();
      for (const ac of midFlightControllersRef.current) ac.abort();
      midFlightControllersRef.current.clear();
      midFlightByQueueRef.current.clear();
      clearStopping();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, navigate]);

  // 对话级订阅（云对话多端同权 B2 · 验收 4）：会话定面后常驻，直到本端自发流来接管。
  // 空闲时它只是挂在那儿收心跳；另一端一起回合，就在同一条连接上重放 + 跟播——「停在空闲对话
  // 上也能自动出现新回合」就落在这里。让位与归位都走 followEpoch（本端流收尾 / 重连 / 回前台）。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 路由/收口经 ref 取最新实现；列入依赖会让每次渲染都重挂连接
  useEffect(() => {
    if (!conversationId || !followReady) return;
    // 主时间线的归属只看 abort 槽，不看 `sending`：插话 / 排队是旁路流，它也会把 `sending`
    // 抬起来，但主路仍归订阅——跟着 `sending` 走会在插话期间把正在跟播的回合掐断。
    if (abortRef.current) return;
    const cid = conversationId;
    const ac = new AbortController();
    followRef.current = ac;
    followTurnRef.current = null;
    const openedAt = Date.now();
    void (async () => {
      try {
        await followConversation(
          cid,
          (event) => {
            if (followRef.current !== ac) return;
            routeFollowEventRef.current(cid, event);
          },
          () => {
            if (followRef.current !== ac) return;
            settleFollowIdleRef.current(cid);
          },
          ac.signal,
        );
        // 服务端正常关流（代理空闲上限）。订阅是长驻的，重新挂上，否则「停在对话上」会静默
        // 失效。用连接时长挡热循环：老后端不认 follow，会立刻 204 结束。
        if (followRef.current !== ac) return;
        if (Date.now() - openedAt >= FOLLOW_REHANG_MIN_MS) {
          // 关流到重挂之间另一端可能整跑完一个回合：重挂后确认空闲时补一次消息窗对账。
          followReconnectedRef.current = true;
          setFollowEpoch((e) => e + 1);
        }
      } catch (e) {
        if (isAbort(e)) return;
        // 已被接管（本端自发流 / 切会话 / 回前台重挂）：陈旧失败不许盖在新连接上。
        if (followRef.current !== ac) return;
        if (stopPhaseRef.current === "stopping") return;
        setError({ text: RECONNECT_BANNER, reconnect: true });
      } finally {
        if (followRef.current === ac) followRef.current = null;
      }
    })();
    return () => {
      ac.abort();
      if (followRef.current === ac) followRef.current = null;
    };
  }, [conversationId, followReady, followEpoch]);

  /**
   * 把订阅上的一帧折进正确的气泡。一条连接跨多个回合，所以回合切分只能按 `message_start` 的
   * `message_id`：新回合的重放段先于它自己的边界注释到达，传输层无从提前分段。
   * 同 id 重开（挂起恢复）仍是同一个气泡——契约如此，勿另开。
   * ref 持有：连接是长驻的，闭包必须每次渲染刷新，否则折叠时读到陈旧的 history/turns。
   */
  const routeFollowEventRef = useRef<(cid: string, event: SSEEvent) => void>(
    () => {},
  );
  routeFollowEventRef.current = (cid, event) => {
    const plan = planFollowSegment({
      cursor: followTurnRef.current,
      head: readSegmentHead(event),
      adoptTurnId: followAdoptRef.current,
      turns,
      newTurnId: () => crypto.randomUUID(),
    });
    followTurnRef.current = plan.cursor;
    // 本端是否「已经知道有这个回合」（打开会话 / 重连时摆的姿势）。知道 = 用户泡本就在历史里，
    // 不必为它回读；不知道 = 另一端刚起的回合，本端快照里没有它的用户泡。
    const expected = expectLiveRunRef.current;
    if (plan.action !== "continue") {
      // 收到回合就说明「有没有在跑」已有答案，续看姿势到此为止；这一段自带整段内容，
      // 重连对账也不必再补（`open` 分支下面自己会回读消息窗）。
      followAdoptRef.current = null;
      expectLiveRunRef.current = false;
      followReconnectedRef.current = false;
    }
    const turnId = plan.cursor.turnId;
    if (plan.action === "reset") {
      // 服务端明令：这一段是本回合的全量重放，先清掉已折的帧再整段重折。
      setTurns((t) => clearLiveTurnEvents(t, turnId));
    } else if (plan.action === "open") {
      openFollowedTurn(cid, turnId, { reloadTranscript: !expected });
    }
    if (plan.action !== "continue") {
      setActiveTurn(turnId);
      setFollowRunning(true);
    }
    appendEventToTurn(turnId, event);
    // 订阅不随回合收口断流，所以 busy 只能由终态帧解除（本端自发流是靠流结束）。
    if (event.type === "message_end") setFollowRunning(false);
  };

  /** 跟播一个回合：新开气泡；另一端起的还要回读历史，把本端快照里没有的用户泡补进来。 */
  const openFollowedTurn = (
    cid: string,
    turnId: string,
    opts: { reloadTranscript: boolean },
  ) => {
    setTurns((t) => [...t, { id: turnId, userText: null, events: [] }]);
    if (!opts.reloadTranscript) return;
    void reloadTranscript(cid, { dropRunningTail: true, dropFolded: true });
  };

  /**
   * 订阅报「连上来时没有回合在跑」（旧 204 的对话级等价物）。本端在等回合（重连 / 重开续看）
   * 说明它在我们连上之前就收口了：撤掉等不到终态的空转气泡，回读持久化终稿。本端没在等则通常
   * 是常态（停在空闲对话上），只有重连挂上的那次要补一趟消息窗对账——见 {@link planFollowIdle}。
   */
  const settleFollowIdleRef = useRef<(cid: string) => void>(() => {});
  settleFollowIdleRef.current = (cid) => {
    const plan = planFollowIdle({
      expectLiveRun: expectLiveRunRef.current,
      adoptTurnId: followAdoptRef.current,
      reconnected: followReconnectedRef.current,
      localStreamActive: abortRef.current !== null,
    });
    followAdoptRef.current = null;
    followReconnectedRef.current = false;
    if (plan.kind === "none") return;
    if (plan.kind === "reconcile") {
      // 只补消息窗：`dropFolded` 撤掉内容已随这次回读进 history 的已收口气泡（history 与 turns
      // 是简单拼接，留着就是双份），仍在折的气泡一个不动。
      void reloadTranscript(cid, { dropFolded: true });
      return;
    }
    expectLiveRunRef.current = false;
    setFollowRunning(false);
    void reloadTranscript(cid, {
      staleTurnId: plan.staleTurnId,
      dropFolded: true,
    });
  };

  /**
   * 回读持久化窗口，history 与 turns 一起写——两者才不会各自旧一半。
   *
   * `dropFolded`：连同已 `message_end` 的 live turn 一起撤。它们的内容这次回读全在 REST 窗里，
   * 留着就是双份（history 与 turns 在渲染上是简单拼接，没有去重）。
   * `staleTurnId`：那个等不到终态的气泡（回合在我们连上之前就收口了），直接撤。
   * `dropRunningTail`：正在跟播的那个回合的 running 影子行由 live 气泡承担，丢掉。
   */
  async function reloadTranscript(
    cid: string,
    opts: {
      staleTurnId?: string | null;
      dropFolded?: boolean;
      dropRunningTail?: boolean;
    } = {},
  ) {
    try {
      const {
        messages,
        hasMoreBefore: more,
        memoryUpdates: mem,
      } = await getMessages(cid);
      if (conversationIdRef.current !== cid) return;
      setHistory(
        opts.dropRunningTail ? dropRunningAssistantTail(messages) : messages,
      );
      setHasMoreBefore(more);
      setMemoryUpdates(mem);
      if (opts.dropFolded) {
        setTurns((t) =>
          dropSettledLiveTurns(removeLiveTurn(t, opts.staleTurnId ?? null)),
        );
      }
    } catch {
      /* 尽力而为：补不出历史也不该打断正在跟播的回合 */
    }
  }

  // 回前台追齐（前后台生命周期）。切后台后 webview 被系统冻结——socket 早断，连空闲看门狗
  // 的计时器都停摆；不主动探活就只能干等超时或用户手点「重连」。先 abort 那条冻着的读，让
  // 它按 isAbort 早退（否则解冻瞬间它会补一条「连接中断」盖在新连接上）。
  //
  // 冻结期间回合可能已经跑完，而对话级订阅空闲不断流、不会再有 204 把状态收回来，所以这里靠
  // 恢复快照（与打开会话同一判据）定面：还在跑就清 partial 重挂续看，没在跑就落地终稿再重新
  // 停上去——停在空闲对话上回前台绝不该卡在 busy。
  useAppForeground(() => {
    if (!conversationId) return;
    const cid = conversationId;
    abortRef.current?.abort();
    followRef.current?.abort();
    // 冻结期间另一端可能整跑完一个回合——它不会被重放，只能靠空闲时的消息窗对账补上。
    followReconnectedRef.current = true;
    const rejoinTurnId = activeTurnIdRef.current;
    if (rejoinTurnId) {
      // 同步摆好「续看」姿势：解冻后本端流的收尾会立刻让订阅归位，那时快照还没回来——
      // 没有这个姿势，仍在跑的回合会被当成新回合另开气泡。
      followAdoptRef.current = rejoinTurnId;
      expectLiveRunRef.current = true;
      setFollowRunning(true);
    }
    setFollowEpoch((e) => e + 1);
    void refreshPaused(cid).then((recovery) => {
      if (conversationIdRef.current !== cid) return;
      if (!rejoinTurnId) return; // 本来就停在空闲对话上：重挂完事，别去动已有内容
      // 快照读不到就按「可能还在跑」处理：续看着，订阅的空闲信号会兜住。
      if (!recovery || recovery.liveRunning) return;
      settleIdleConversation(cid);
    });
  });

  /**
   * 确认对话空闲（冻结期间回合已跑完）：撤掉等不到终态的空转气泡、落地终稿、清 busy。
   * 订阅本身不用动——它停在这条对话上，等的就是下一个回合。
   */
  function settleIdleConversation(cid: string) {
    setError(null);
    expectLiveRunRef.current = false;
    followAdoptRef.current = null;
    followTurnRef.current = null;
    // 这趟回读就是重连对账，别让订阅的空闲信号再补一次。
    followReconnectedRef.current = false;
    setFollowRunning(false);
    void reloadTranscript(cid, {
      staleTurnId: activeTurnIdRef.current,
      dropFolded: true,
    });
  }

  // Page strictly older messages in above the window (历史上翻分页). The oldest loaded
  // row's created_at is the cursor; we anchor the viewport (distance-from-bottom) so the
  // prepend doesn't yank the scroll. Re-entrancy-guarded; the chat keeps working if it fails.
  async function loadOlder() {
    if (!conversationId || loadingOlder || !hasMoreBefore) return;
    const oldest = history?.[0];
    if (!oldest) return;
    preparePrepend();
    setLoadingOlder(true);
    try {
      const { messages, hasMoreBefore: more } = await getMessages(
        conversationId,
        oldest.created_at,
      );
      setHistory((h) => [...messages, ...(h ?? [])]);
      setHasMoreBefore(more);
    } catch (e) {
      cancelPrepend();
      setError({
        text: e instanceof Error ? e.message : "加载更早消息失败",
      });
    } finally {
      setLoadingOlder(false);
    }
  }

  // The live turn's projection drives the interactive pause surface: while a stream is
  // held open (`busy`) and the fold reports a gate, the PauseCard below offers
  // resolution — equally for a fresh turn and one rejoined via reattach (a run paused at
  // an approval shows its live card on reconnect).
  // 出队开跑后「最后一项」可能是新 turn——投影须跟 activeTurnId。
  const liveTurn =
    turns.find((t) => t.id === activeStreamTurnId) ??
    (turns.length > 0 ? turns[turns.length - 1] : null);
  const liveProjection = useMemo(
    () => (liveTurn ? fold(liveTurn.events) : null),
    [liveTurn],
  );
  const interruptible = isLiveInterruptible(liveProjection);
  // busy 默认 queue（defaultDelivery）；插队轻链显式 steer；interruptible 仅作文案启发式。
  // 挂起即收口 (②, Phase 3): hot-path cards resolve live in-stream; cold path
  // (ask_user / plan_review / team_preview) finalizes and uses ResumeCard.
  const liveInteractions = busy
    ? (liveProjection?.interactions ?? []).filter((i) => i.status === "pending")
    : [];
  const liveApprovals = liveInteractions.filter(
    (i): i is Extract<ProjectedInteraction, { kind: "approval" }> =>
      i.kind === "approval",
  );
  const approvalCards =
    liveApprovals.length > 0
      ? liveApprovals
      : recoveredInteractions
          .map(recoveredApprovalPending)
          .filter((x): x is NonNullable<typeof x> => x != null);

  // 阶段推进卡（批 B）：幕 1 收尾后耐久展示；live fold 优先，冷开走 recovery。
  const stageCards = useMemo(() => {
    const fromLive = (liveProjection?.interactions ?? []).filter(
      (i): i is Extract<ProjectedInteraction, { kind: "stage_card" }> =>
        i.kind === "stage_card" &&
        (i.status === "pending" || i.status === "orphaned"),
    );
    if (fromLive.length > 0) return fromLive;
    if (busy) return [];
    return recoveredInteractions
      .map(recoveredStageCard)
      .filter((x): x is NonNullable<typeof x> => x != null);
  }, [liveProjection, recoveredInteractions, busy]);

  // 冷恢复 escalation：composer 上方可答卡（优先于 HistoryAssistant 时间线 interactive）。
  const escalationCards = useMemo(() => {
    if (busy) return [];
    return recoveredInteractions
      .map(recoveredEscalation)
      .filter((x): x is NonNullable<typeof x> => x != null);
  }, [recoveredInteractions, busy]);

  // Live cold ResumeCard: Interaction pending (+ stamp) is authority; recovery paused = shell.
  const coldHosts = useMemo((): ColdResumeHost[] => {
    const hosts: ColdResumeHost[] = [];
    for (const m of history ?? []) {
      if (m.role === "assistant") {
        hosts.push({
          role: "assistant",
          id: m.id,
          serverMessageId: m.id,
        });
      }
    }
    for (const t of turns) {
      hosts.push({
        role: "assistant",
        id: t.id,
        serverMessageId: extractMessageId(t.events),
      });
    }
    return hosts;
  }, [history, turns]);

  const visibleResumes = useMemo(() => {
    if (!conversationId) return [];
    let userMessage = "";
    let userMessageId = "";
    for (let i = (history?.length ?? 0) - 1; i >= 0; i--) {
      const m = history?.[i];
      if (m?.role === "user") {
        userMessage = m.content ?? "";
        userMessageId = m.id;
        break;
      }
    }
    for (let i = turns.length - 1; i >= 0; i--) {
      const t = turns[i];
      if (t?.userText) {
        userMessage = t.userText;
        break;
      }
    }
    return selectVisibleColdResumes({
      conversationId,
      byId: coldById,
      paused,
      hosts: coldHosts,
      userMessage,
      userMessageId,
    });
  }, [conversationId, coldById, paused, coldHosts, history, turns]);

  // 摆出去的卡登记进 ref，供收口事件判归属（只给用户真看得见的卡立「已由另一端处理」）。
  visibleCardIdsRef.current = new Set<string>([
    ...approvalCards.map((c) => c.id),
    ...escalationCards.map((c) => c.id),
    ...stageCards.map((c) => c.id),
    ...visibleResumes.map((p) => p.checkpoint_id),
  ]);

  // Cold actionable pending with stamp ⇒ unlock composer (desktop finalizeGenerating
  // parity). Submitting / resume_deferred wait keeps 提交中态 — do not clear sending.
  useEffect(() => {
    if (!sending) return;
    const hasActionable = visibleResumes.some(
      (p) =>
        (p.interactionStatus ?? "pending") === "pending" &&
        !p.deferredBusyReason,
    );
    if (hasActionable) setSending(false);
  }, [visibleResumes, sending]);

  // Stage picked files (composer 附件). Text is extracted; images/binary are resident-first
  // (upload when a conversation exists, else hold File until first send). The input is reset
  // so re-picking the same file fires onChange again.
  async function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length === 0) return;
    setAttachError(null);
    const added: MessageAttachment[] = [];
    const refused: string[] = [];
    for (const file of files) {
      const res = await prepareAttachment(file, conversationId ?? null);
      if (res.ok) added.push(res.attachment);
      else refused.push(`${file.name}：${res.reason}`);
    }
    if (added.length > 0) setAttachments((prev) => [...prev, ...added]);
    if (refused.length > 0) setAttachError(refused.join("；"));
  }

  function removeAttachment(key: string) {
    setAttachments((prev) => prev.filter((a) => attachmentDraftKey(a) !== key));
  }

  // Ask / debate handoff → fill the composer (don't auto-send: let the user edit first).
  // Appends after a space when text is already typed, so a fill never clobbers it.
  function fillComposer(text: string) {
    setInput((prev) => (prev.trim() ? `${prev} ${text}` : text));
    composerInputRef.current?.focus();
  }

  // 会话级模型组合 (定案 B): apply a concrete profile from the ModelPicker. Remembered as
  // last-used. An open conversation is PATCHed now; a draft holds it until startDraft
  // snapshots it via POST model_profile_id.
  async function onSelectProfile(profileId: string) {
    setPickerOpen(false);
    setLastModelProfileId(profileId);
    if (!conversationId) {
      setCurrentProfileId(profileId);
      return;
    }
    const previous = currentProfileId;
    setCurrentProfileId(profileId);
    try {
      const updated = await setConversationModelProfile(
        conversationId,
        profileId,
      );
      setCurrentProfileId(updated.model_profile_id ?? null);
    } catch (e) {
      setCurrentProfileId(previous);
      setError({ text: e instanceof Error ? e.message : "切换模型组合失败" });
    }
  }

  // 直接对话: a draft (no conversationId) lazily creates a conversation on first send, then
  // routes to /c/:id where the remounted page POST+streams the message (via pendingFirstSend).
  // Keeps the empty-shell-conversation cost off「新建」— the row only exists once you commit.
  async function startDraft() {
    const raw = input;
    const text = raw.trim();
    if (!hasSendableDraft(text, attachments) || conversationId || busy) return;
    // 同步栓：必须在第一个 await 之前置位，否则同帧的第二次提交照样进得来（见 ref 注释）。
    if (creatingConversationRef.current) return;
    creatingConversationRef.current = true;
    const outgoing = attachments;
    const outgoingMentions = agentMentions;
    if (!draftRequestIdRef.current) {
      draftRequestIdRef.current = crypto.randomUUID();
    }
    const clientRequestId = draftRequestIdRef.current;
    pendingEmptyRollback = null;
    setError(null);
    setSending(true);
    // 先收草稿、先摆气泡：手机上创建 + 首发要走两趟网，等 POST 回来才有反应会让人以为没发出去。
    setInput("");
    setAttachments([]);
    setAgentMentions([]);
    setAttachError(null);
    setDraftPending({
      text,
      attachments: outgoing,
      agentMentions: outgoingMentions,
    });
    try {
      // 定案 B: snapshot chosen / last-used profile at create (omit → server writes then-default).
      const id = await createConversation(undefined, {
        ...(draftFolder ? { folder_id: draftFolder.id } : {}),
        ...(permissionDraftTouched ? { permission_axes: permissionAxes } : {}),
        ...(currentProfileId ? { model_profile_id: currentProfileId } : {}),
        client_request_id: clientRequestId,
      });
      pendingFirstSend = {
        id,
        text,
        attachments: outgoing,
        agentMentions: outgoingMentions,
        folder: draftFolder,
      };
      // 这份草稿到此为止：下一份草稿换新键。
      draftRequestIdRef.current = null;
      navigate(`/c/${id}`, { replace: true });
    } catch (e) {
      // 整份还给用户（键留着：重发的还是同一份草稿，服务端同键不会再建一条）。
      setInput(raw);
      setAttachments(outgoing);
      setAgentMentions(outgoingMentions);
      setDraftPending(null);
      setError({ text: e instanceof Error ? e.message : "创建会话失败" });
      setSending(false);
      creatingConversationRef.current = false;
    }
  }

  // Submit dispatch: a draft creates-then-routes (startDraft); an open conversation streams
  // in place (send). The composer / Enter both go through here.
  // 生成中：默认 queue；显式插队走 sendForcedSteer。
  const onSubmit = (deliveryOverride?: MessageDelivery) => {
    if (conversationId) void send(undefined, deliveryOverride);
    else void startDraft();
  };

  const sendForcedSteer = () => {
    if (!conversationId || !hasSendableDraft(input, attachments)) return;
    void send(undefined, "steer");
  };

  /** 取消 FIFO 排队项（Stop ≠ 取消排队）：只清条 + abort mid-flight。 */
  function applyQueueCancelLocal(entry: QueuedTurnEntry) {
    removeQueuedTurn(entry.conversationId, entry.queueId);
    const ac = midFlightByQueueRef.current.get(entry.queueId);
    if (ac) {
      ac.abort();
      midFlightByQueueRef.current.delete(entry.queueId);
      midFlightControllersRef.current.delete(ac);
    }
  }

  // Stream a turn into the open conversation. `override` carries a draft's first message
  // across the remount (it bypasses the input state, which the new page doesn't have).
  // 生成中再发走 mid-flight（turn_queued / user_interjection），composer 不禁发。
  async function send(
    override?: {
      text: string;
      attachments: MessageAttachment[];
      agentMentions?: PendingAgentMention[];
      folder?: DraftFolder | null;
      preserveComposer?: boolean;
    },
    deliveryOverride?: MessageDelivery,
  ): Promise<boolean> {
    const text = (override?.text ?? input).trim();
    const outgoing = override?.attachments ?? attachments;
    const outgoingMentions = override?.agentMentions ?? agentMentions;
    const createdFolder = override?.folder ?? null;
    if (!hasSendableDraft(text, outgoing) || !conversationId) return false;
    if (stopPhaseRef.current === "stopping") return false;
    // Interactive mid-flight while a turn is already streaming (本端自发或跟播另一端的都算).
    if (!override && turnInFlight) {
      const delivery = deliveryOverride ?? defaultDelivery({ busy: true });
      void sendWhileBusy(text, delivery);
      return false;
    }
    let wireAttachments: Array<Omit<MessageAttachment, "fileBlob">> = [];
    if (outgoing.length > 0) {
      const finalized = await finalizeAttachmentsForSend(
        conversationId,
        outgoing,
      );
      if (!finalized.ok) {
        setAttachError(finalized.reason);
        return false;
      }
      wireAttachments = finalized.attachments;
    }
    if (!override) {
      setInput("");
      setAttachments([]);
      setAgentMentions([]);
    }
    setAttachError(null);
    setError(null);
    clearStopping();
    markStreamStart();
    primaryActiveRef.current = true;
    jumpToBottom();
    const turnId = crypto.randomUUID();
    setActiveTurn(turnId);
    setTurns((t) => [
      ...t,
      {
        id: turnId,
        userText: text,
        events: [],
        attachments: wireAttachments.map((a) => ({
          name: a.name,
          truncated: a.truncated,
        })),
        agentMentions: outgoingMentions.map((a) => ({
          agentId: a.agentId,
          role: a.role,
        })),
      },
    ]);

    const ac = new AbortController();
    claimLocalStream(ac);
    const collected: SSEEvent[] = [];
    const restoreComposer = () => {
      setTurns((t) => removeLiveTurn(t, turnId));
      if (override?.preserveComposer) return;
      setInput(text);
      setAttachments(outgoing);
      setAgentMentions(outgoingMentions);
    };
    const maybeDismantleEmptyConversation = (err: ChatError) => {
      // 闭包里的 turns 还没有本发刚 push 的那条：回滚后 leftover = 回滚前的其它 live turn。
      const leftoverLiveTurns = turns.filter((t) => t.id !== turnId);
      const hist = historyRef.current;
      // null = 历史还没拉回来，不能当「空会话」拆（否则会误删已有对话）。
      if (
        hist === null ||
        leftoverLiveTurns.length > 0 ||
        hist.length > 0 ||
        !conversationId
      ) {
        return;
      }
      const cid = conversationId;
      void (async () => {
        try {
          await deleteConversation(cid);
        } catch {
          return; // 删失败留在 /c/:id；错误条已在本页
        }
        pendingEmptyRollback = {
          error: err,
          text,
          attachments: outgoing,
          agentMentions: outgoingMentions,
          folder: createdFolder,
        };
        navigate("/", {
          replace: true,
          ...(createdFolder
            ? {
                state: {
                  draftFolderId: createdFolder.id,
                  draftFolderName: createdFolder.name,
                },
              }
            : {}),
        });
      })();
    };
    let sentOk = false;
    try {
      await streamMessage(
        conversationId,
        text,
        (event) => {
          collected.push(event);
          appendEventToTurn(turnId, event);
        },
        ac.signal,
        wireAttachments.length > 0 ? wireAttachments : undefined,
        "steer",
        outgoingMentions.length > 0
          ? toOutgoingAgentMentions(outgoingMentions)
          : undefined,
      );
      // SSE error 后 stream 常 resolve 不 throw：本发已落库 + 空失败 + Class B 码也要回滚。
      const zero = inspectZeroOutputSendRollback(collected);
      if (zero.rollback) {
        const err: ChatError | null = zero.errorMessage
          ? withTurnSupport(
              {
                text: zero.errorMessage,
                action:
                  errorActionForCode(zero.errorCode, {
                    credentialSource: zero.credentialSource,
                    message: zero.errorMessage,
                  }) ?? undefined,
              },
              conversationId,
              collected,
            )
          : null;
        if (err) setError(err);
        restoreComposer();
        if (err) maybeDismantleEmptyConversation(err);
      } else {
        sentOk = true;
      }
    } catch (e) {
      if (isAbort(e)) return false; // conversation switch — partial stays, server salvages
      // Pre-stream refusal (402 LLM_KEY_REQUIRED etc.) — surface banner +「去配置」, do not
      // treat as a dropped live run (nothing started).
      if (e instanceof StreamHttpError) {
        const d = describeStreamHttpError(e);
        const err: ChatError = withTurnSupport(
          {
            text: d.message,
            action: d.action ?? undefined,
          },
          conversationId,
          collected,
        );
        setError(err);
        if (isUnstartedSendRefusal(e)) {
          restoreComposer();
          maybeDismantleEmptyConversation(err);
        } else if (inspectZeroOutputSendRollback(collected).rollback) {
          restoreComposer();
          maybeDismantleEmptyConversation(err);
        }
        return false;
      }
      // 诚实停止等待中断流：不自动重连，保持 stopping 等引擎终态。
      if (isStoppingNow()) return false;
      // A mid-stream drop no longer means the turn died (slice 1a: it runs detached) —
      // rejoin it (1b) rather than resending, which would double-run it.
      reconnect();
      sentOk = true;
    } finally {
      signalPrimaryIdle();
      // Only settle sending if still the current op — a switch / takeover (reconnect /
      // mid-flight turn2) replaced the controller and owns the state now.
      if (releaseLocalStream(ac)) {
        markStreamEnd();
      } else {
        inflightRef.current = Math.max(0, inflightRef.current - 1);
      }
    }
    return sentOk;
  }

  /** 生成中发送：独立 POST SSE；queue → 仅条；出队开跑再进主时间线用户泡。 */
  async function sendWhileBusy(
    text: string,
    delivery: MessageDelivery,
    extras?: { preserveComposer?: boolean },
  ): Promise<false | "received" | "queued"> {
    if (!conversationId) return false;
    const preserve = extras?.preserveComposer === true;
    const outgoing = preserve ? [] : attachments;
    const outgoingMentions = preserve ? [] : agentMentions;
    let wireAttachments: Array<Omit<MessageAttachment, "fileBlob">> = [];
    if (outgoing.length > 0) {
      const finalized = await finalizeAttachmentsForSend(
        conversationId,
        outgoing,
      );
      if (!finalized.ok) {
        setAttachError(finalized.reason);
        return false;
      }
      wireAttachments = finalized.attachments;
    }
    // ack 前不清：等 turn_queued / steer / 插话确认后再清（勿等整段泵）。
    setAttachError(null);
    setError(null);
    jumpToBottom();
    markStreamStart();

    const ac = new AbortController();
    midFlightControllersRef.current.add(ac);
    let queuedTurnId: string | null = null;
    let trackedQueueId: string | null = null;
    let composerCleared = false;
    const clearComposerOnAck = () => {
      if (preserve || composerCleared) return;
      composerCleared = true;
      setInput("");
      setAttachments([]);
      setAgentMentions([]);
    };

    try {
      const result = await sendMidFlightMessage(
        conversationId,
        text,
        {
          onLiveEvent: (event) => {
            // 插话 ack：清输入并写入当前主路；turn_queued 由 onQueued 处理。
            if (event.type === "turn_queued") return;
            if (event.type === "user_interjection") {
              clearComposerOnAck();
            }
            appendEventToTurn(activeTurnIdRef.current, event);
          },
          onQueued: (info) => {
            // 仅 QueuedTurnsBar；排队期不插主时间线用户泡。
            clearComposerOnAck();
            trackedQueueId = info.queueId;
            midFlightByQueueRef.current.set(info.queueId, ac);
            upsertQueuedTurn({
              queueId: info.queueId,
              conversationId,
              content: text,
              position: info.position,
              queueDepth: info.queueDepth,
              degradedFrom: info.degradedFrom,
            });
          },
          beginTurn2: () => {
            // 出队开跑：插入主时间线用户泡，并接管 abort 槽。
            // 条由 turn_queue_started（sink 首帧）清，勿在此猜出队。
            if (!queuedTurnId) {
              const turnId = crypto.randomUUID();
              queuedTurnId = turnId;
              setTurns((t) => [
                ...t,
                {
                  id: turnId,
                  userText: text,
                  events: [],
                  attachments: wireAttachments.map((a) => ({
                    name: a.name,
                    truncated: a.truncated,
                  })),
                  agentMentions: outgoingMentions.map((a) => ({
                    agentId: a.agentId,
                    role: a.role,
                  })),
                },
              ]);
            }
            setActiveTurn(queuedTurnId);
            claimLocalStream(ac);
          },
          onTurn2Event: (event) => {
            appendEventToTurn(queuedTurnId, event);
          },
          isPrimaryIdle: () => !primaryActiveRef.current,
          waitPrimaryIdle,
        },
        wireAttachments.length > 0 ? wireAttachments : undefined,
        ac.signal,
        delivery,
        outgoingMentions.length > 0
          ? toOutgoingAgentMentions(outgoingMentions)
          : undefined,
      );
      if (result.kind === "blocked") {
        setError({ text: result.message ?? "请先处理待确认事项" });
        return false;
      }
      if (result.kind === "error") {
        setError({ text: result.message });
        return false;
      }
      if (result.kind === "received" || result.kind === "queued") {
        // 泵已结束时仍兜底清一次（ack 回调已清则 no-op）。
        clearComposerOnAck();
        return result.kind;
      }
      return false;
    } finally {
      midFlightControllersRef.current.delete(ac);
      if (trackedQueueId) {
        midFlightByQueueRef.current.delete(trackedQueueId);
      }
      releaseLocalStream(ac);
      markStreamEnd();
    }
  }

  // 诚实停止闭环：进入「停止中」可见态，POST /stop，保持 SSE 等后端终态（不本地 abort /
  // 不伪造终态）。/stop 失败 → 回滚 idle + 诚实失败提示，可再点停止。
  function stop() {
    if (!busy && stopPhaseRef.current !== "stopping") return;
    if (!conversationId) {
      // Draft edge: no server run yet — local abort only.
      abortRef.current?.abort();
      return;
    }
    setError(null);
    applyStopPhase(reduceStopPhase(stopPhaseRef.current, "request_stop"));
    void stopConversation(conversationId).catch(() => {
      if (stopPhaseRef.current !== "stopping") return;
      applyStopPhase(reduceStopPhase("stopping", "stop_http_fail"));
      setError({ text: STOP_FAILED_MESSAGE });
    });
  }

  // Rejoin a turn whose live stream dropped mid-flight (实时重连续看 C1 · slice 1b). Marks the
  // partial bubble for the conversation subscription to 认领 and re-hangs that subscription —
  // the replay段 的段首自带全量重放标记，气泡由它清（本端不再抢着先清，重连到一半也不会白屏）；
  // 清完整段重折，live tail 接着跑。回合真的已经收口时不再有 204 兜底，改由订阅的空闲信号收口
  // （settleFollowIdle：撤气泡 + 回读终稿）。一再失败就出手动「重连」。
  // 出队开跑后队尾可能是新 turn——目标须跟 activeTurnIdRef（与投影约定一致），禁 turns[-1]。
  function reconnect() {
    if (!conversationId) return;
    setError(null);
    clearStopping();
    // SSE 重连后对账排队条（权威 GET）；消息窗对账在订阅报空闲时补（planFollowIdle）。
    reconcileQueuedRef.current(conversationId);
    followReconnectedRef.current = true;
    const reconnectTurnId = activeTurnIdRef.current;
    if (reconnectTurnId) {
      setActiveTurn(reconnectTurnId);
      followAdoptRef.current = reconnectTurnId;
      expectLiveRunRef.current = true;
      setFollowRunning(true);
    }
    // 本端自发流让位（手点「重连」时它可能还卡着）：它的收尾会归还槽位并再促一次重挂。
    abortRef.current?.abort();
    followRef.current?.abort();
    setFollowEpoch((e) => e + 1);
  }

  // P4: interrupted salvage → regenerate from the preceding user message (same endpoint
  // as desktop runRegenerate; no new API).
  async function retryInterrupted() {
    if (!conversationId || !history || busy) return;
    const last = history[history.length - 1];
    if (!last || last.role !== "assistant") return;
    let userId: string | null = null;
    for (let i = history.length - 2; i >= 0; i--) {
      if (history[i].role === "user") {
        userId = history[i].id;
        break;
      }
    }
    if (!userId) return;
    setError(null);
    clearStopping();
    setSending(true);
    // Drop interrupted assistant from history; live turn carries the regenerate stream.
    setHistory((h) => (h ? h.slice(0, -1) : h));
    const turnId = crypto.randomUUID();
    setActiveTurn(turnId);
    setTurns([
      {
        id: turnId,
        userText: null,
        events: [],
      },
    ]);
    const ac = new AbortController();
    claimLocalStream(ac);
    try {
      await regenerateStream(
        conversationId,
        userId,
        (event) => appendEventToTurn(turnId, event),
        ac.signal,
      );
    } catch (e) {
      if (isAbort(e)) return;
      if (e instanceof StreamHttpError) {
        const d = describeStreamHttpError(e);
        setError(
          withTurnSupport(
            {
              text: d.message,
              action: d.action ?? undefined,
            },
            conversationId,
            [],
          ),
        );
        if (isUnstartedSendRefusal(e)) {
          setTurns((t) => removeLiveTurn(t, turnId));
        }
        return;
      }
      setError({ text: e instanceof Error ? e.message : "重试失败" });
    } finally {
      if (releaseLocalStream(ac)) setSending(false);
    }
  }

  // 挂起即收口 (②): re-read the conversation's recovery snapshot — used when a live stream
  // ends at a checkpoint (message_end finish_reason=paused) so the just-finalized turn's
  // durable ResumeCard surfaces. Live paint prefers cold IX; this syncs the recovery shell.
  // Cheap + idempotent; best-effort. 回前台还拿它的 `liveRunning` 定「回合是否还在跑」——
  // 对话级订阅空闲不断流，没有 204 可当那个判据了。读失败返回 null（调用方按未知处理）。
  async function refreshPaused(cid: string): Promise<TurnRecovery | null> {
    try {
      const r = await getRecovery(cid);
      setPaused(r.paused);
      for (const p of r.paused) {
        if (!isColdResumeKind(p.kind)) continue;
        upsertColdRequired({
          kind: p.kind,
          conversationId: cid,
          messageId: p.message_id,
          payload: pausedSummaryToRequiredPayload(p),
          status: "pending",
        });
      }
      return r;
    } catch {
      /* best-effort: never break the just-finished turn on a recovery refresh */
      return null;
    }
  }

  // Continue a durably-paused turn (结构化挂起 2b). Option A: reuse the paused
  // assistant by server message_id (same bubble → streaming) — never push a second
  // assistant turn (dual TeamView). Desktop parity: resumePausedAssistant / runResume.
  // Busy slot → EPHEMERAL resume_deferred on the same SSE (not 409); card stays as
  // 「放行已记下…」until claim+续跑. Mid-stream drop rejoins rather than re-resumes.
  async function resume(
    messageId: string,
    decision: CheckpointDecision,
    note: string,
    selected: string[] = [],
    amendments?: TeamPreviewAmendments,
  ) {
    if (!conversationId || busy) return;
    const coldTargets = listColdPending(conversationId).filter(
      (e) => e.messageId === messageId,
    );
    const resolution = { decision, note, selected };
    for (const e of coldTargets) {
      // 登记在 POST 之前：随后回来的 `*_resolved` / `resume_deferred` 才认得出是自己点的。
      markLocalSettlement(e.id);
      markColdSubmitting({
        kind: e.kind,
        id: e.id,
        resolution,
      });
    }
    setPaused((p) => p.filter((x) => x.message_id !== messageId));
    setError(null);
    clearStopping();
    setSending(true);
    const prepared = prepareResumePausedTurn({
      messageId,
      turns,
      history,
      newTurnId: crypto.randomUUID(),
    });
    const turnId = prepared.turnId;
    // setActiveTurn clears host stamp when the active turn id changes — re-seal
    // after so ask_user continue → same-turn team_preview keeps the projection key.
    setActiveTurn(turnId);
    hostServerMessageIdRef.current = messageId;
    setTurns(prepared.turns);
    if (prepared.history !== history) setHistory(prepared.history);

    const ac = new AbortController();
    claimLocalStream(ac);
    try {
      // stop / adjust / ask·debate：不带组队修正；delegate continue 才附写盘收紧
      //（确认面不附 excluded_run_ids / model_overrides；契约类型字段仍可保留）。
      const body: ResumeTurnBody = { decision, note, selected };
      if (decision === "continue" && amendments) {
        const hasWrite =
          (amendments.write_capability_overrides?.length ?? 0) > 0;
        const hasModels =
          !!amendments.model_overrides &&
          Object.keys(amendments.model_overrides).length > 0;
        if (hasWrite || hasModels) {
          if (hasWrite) {
            body.write_capability_overrides =
              amendments.write_capability_overrides;
          }
          if (hasModels) body.model_overrides = amendments.model_overrides;
        }
      }
      await resumeStream(
        conversationId,
        messageId,
        body,
        (event) => appendEventToTurn(turnId, event),
        ac.signal,
      );
      // Stream settled without an earlier message_start settle — drop submitting cards.
      for (const e of coldTargets) {
        if (getColdInteraction(e.id)?.status === "submitting") {
          markColdResolved({ kind: e.kind, id: e.id, resolution });
        }
      }
    } catch (err) {
      // 挂起帧真的不在了（超保留期被清理 / 回合已重新生成或删除）。「已由另一端处理」不再走
      // 这条路——那种幂等成功现在回 200 + EPHEMERAL `resume_settled`，流正常收尾后由上面按结果
      // 收卡。这里剩的是诚实失效：卡作废（不是「被答了」），也不放回可点——放回只会请用户一点
      // 再点、次次 404。到底是哪一种由后端那两句话说清，挂上错误条别丢。别当掉线去重连。
      // 对齐桌面 isPausedFrameGone + interactionSubmit 的 markOrphaned 分支。
      if (isPausedFrameGone(err)) {
        for (const entry of coldTargets) {
          markColdOrphaned(entry.id, {
            kind: entry.kind,
            conversationId,
            messageId: entry.messageId,
          });
        }
        setError({ text: describeStreamHttpError(err).message });
        return;
      }
      for (const entry of coldTargets) {
        const cur = getColdInteraction(entry.id);
        // Deferred 后 settlement 已锁：断连不清「已记下」；仅 claim 前失败才恢复可编辑。
        if (cur?.deferredBusyReason) continue;
        reopenColdPending(entry.id);
      }
      if (isAbort(err)) return;
      if (stopPhaseRef.current === "stopping") return;
      reconnect();
    } finally {
      if (releaseLocalStream(ac)) setSending(false);
    }
  }

  /** CEO rate-limit pause continue — not checkpoint ResumeCard / `/resume`. */
  async function continueCeoPaused(messageId: string) {
    if (!conversationId || busy || !messageId) return;
    setError(null);
    clearStopping();
    setSending(true);
    const prepared = prepareResumePausedTurn({
      messageId,
      turns,
      history,
      newTurnId: crypto.randomUUID(),
    });
    const turnId = prepared.turnId;
    setActiveTurn(turnId);
    setTurns(prepared.turns);
    if (prepared.history !== history) setHistory(prepared.history);

    const ac = new AbortController();
    claimLocalStream(ac);
    try {
      await continueStream(
        conversationId,
        messageId,
        (event) => appendEventToTurn(turnId, event),
        ac.signal,
      );
    } catch (err) {
      if (isAbort(err)) return;
      if (stopPhaseRef.current === "stopping") return;
      if (err instanceof StreamHttpError) {
        void reloadTranscript(conversationId, { dropFolded: true });
        throw err;
      }
      // 掉线走重连看回合，但继续本身没成功——必须抛出让 PausedContinueCard 解锁，否则
      // 无异常被当成成功，按钮停在「继续中…」无法重试。
      reconnect();
      throw new Error("连接中断，请重试");
    } finally {
      if (releaseLocalStream(ac)) setSending(false);
    }
  }

  /** 推进卡 resolve：起新回合 SSE（机制直起辩论或回灌调研）。 */
  async function runStageCard(
    stageCardId: string,
    body: {
      decision: "start_debate" | "research_first";
      note?: string;
      motionOverride?: string | null;
    },
  ): Promise<void> {
    if (!conversationId || busy) return;
    setRecoveredInteractions((prev) =>
      prev.filter((a) => a.id !== stageCardId),
    );
    // 登记在 POST 之前：随后回来的 `stage_card_resolved` 才认得出是自己点的。
    markLocalSettlement(stageCardId);
    setError(null);
    clearStopping();
    setSending(true);
    const turnId = crypto.randomUUID();
    setActiveTurn(turnId);
    setTurns((t) => [...t, { id: turnId, userText: null, events: [] }]);
    const ac = new AbortController();
    claimLocalStream(ac);
    try {
      await resolveStageCardStream(
        conversationId,
        stageCardId,
        body,
        (event) => appendEventToTurn(turnId, event),
        ac.signal,
      );
    } catch (e) {
      if (isAbort(e)) return;
      if (e instanceof StreamHttpError && e.status === 422) {
        // 检定失败：撤回本空回合（按 id，禁 slice(-1)——期间可能已插排队泡）
        setTurns((t) => removeLiveTurn(t, turnId));
        releaseLocalStream(ac);
        setSending(false);
        throw e;
      }
      if (
        e instanceof StreamHttpError &&
        (e.status === 404 || e.status === 410)
      ) {
        // 卡已被另一端推进过（先到先得）：撤回本空回合并如实收口，别当掉线去重连。
        setTurns((t) => removeLiveTurn(t, turnId));
        noteRemoteSettlementFromReceipt({
          interactionId: stageCardId,
          conversationId,
          kind: "stage_card",
        });
        return;
      }
      if (stopPhaseRef.current === "stopping") return;
      reconnect();
    } finally {
      if (releaseLocalStream(ac)) setSending(false);
    }
  }

  // 当前组合：会话快照 → account default → placeholder（「＋」菜单）。
  const currentProfile = resolveDisplayProfile(modelProfiles, currentProfileId);
  const modelLabel = currentProfile?.name ?? "默认组合";
  const modelIsPreset = currentProfile?.kind === "system";
  const permissionLabel = axesShortLabel(permissionAxes);
  // 建会话在途：草稿已经收进 draftPending 摆成气泡，composer 停在「发送中」等这一趟走完。
  const creatingConversation = draftPending !== null;
  const composerLocked =
    history === null || stopPhase === "stopping" || creatingConversation;
  const hasDraft = hasSendableDraft(input, attachments);
  const trailing = composerTrailingSlots({
    busy,
    hasDraft,
    voiceSupported: voice.isSupported,
    voiceActive: voice.isRecording || voice.state === "processing",
  });

  // 幂等键跟草稿走：用户自己把草稿清空 → 下次发送换新键（提交时的清空不算，那份还在飞）。
  useEffect(() => {
    if (!hasDraft && !creatingConversationRef.current) {
      draftRequestIdRef.current = null;
    }
  }, [hasDraft]);

  // Auto-grow textarea (cap ~5 lines) so multi-line drafts don't steal the button row.
  // useLayoutEffect + assign `el.value = input` so the dep is real (not a fake trigger)
  // and programmatic setInput (voice/chip/clear) still remeasures before paint.
  useLayoutEffect(() => {
    const el = composerInputRef.current;
    if (!el) return;
    el.value = input;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [input]);

  // 「记忆已更新」卡的落点 (③ §1.6): 按 anchorAt（固化窗口末尾）插到它所总结那一轮的末尾，
  // 锚不到更晚回合的留在线程尾部。
  const memoryAnchors = useMemo(
    () => placeMemoryUpdates(history ?? [], memoryUpdates),
    [history, memoryUpdates],
  );

  const latestOutlet = useMemo(() => {
    const empty: {
      owns: boolean;
      pause: boolean;
      outcome: TurnOutcome | null;
      supportIds: SupportDiagnosticIds;
    } = { owns: false, pause: false, outcome: null, supportIds: {} };
    const live = [...turns].reverse().find((t) => t.events.length > 0);
    if (live) {
      const isLiveStream =
        busy && activeStreamTurnId != null && live.id === activeStreamTurnId;
      if (isLiveStream) return empty;
      const out = resolveTurnOutcomeFromJournal({ events: live.events });
      return {
        owns: turnOwnsUserFacingOutlet(out),
        pause: isCeoContinuePause(out),
        outcome: out,
        supportIds: supportIdsFromEvents(conversationId, live.events),
      };
    }
    const hist = [...(history ?? [])]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!hist) return empty;
    const out = resolveTurnOutcomeFromJournal({
      events: hist.runs?.events ?? [],
      content: hist.content,
      finishReason: hist.runs?.finish_reason,
      errorCode: hist.runs?.error?.code,
      errorMessage: hist.runs?.error?.message,
      paused: hist.paused,
      wireResult: hist.outcome,
    });
    return {
      owns: turnOwnsUserFacingOutlet(out),
      pause: isCeoContinuePause(out),
      outcome: out,
      supportIds: historySupportIds(
        hist,
        conversationId ?? null,
        undefined,
        precedingUserMessageId(history ?? [], hist.id),
      ),
    };
  }, [turns, history, busy, activeStreamTurnId, conversationId]);
  const latestOwnsOutlet = latestOutlet.owns;
  const latestPauseSurface = latestOutlet.pause;
  const composerOutcomeHint =
    !busy &&
    !creatingConversation &&
    latestOutlet.outcome &&
    turnOutcomeShowsComposerHint(latestOutlet.outcome)
      ? latestOutlet.outcome.notice
      : null;

  // 一条历史消息的渲染（用户气泡 / 助手回合）。抽成函数是为了在它前面插锚定的记忆卡——
  // 被隐藏的消息（系统收口、空内容）返回 null，卡不能跟着一起消失。
  function renderHistoryRow(m: MessageDetail, isLast: boolean) {
    if (m.role !== "user")
      return (
        <HistoryAssistant
          key={m.id}
          m={m}
          conversationId={conversationId ?? null}
          userMessageId={precedingUserMessageId(history ?? [], m.id)}
          onFill={fillComposer}
          isLast={isLast}
          onRetry={isLast ? () => void retryInterrupted() : undefined}
          onContinue={
            conversationId ? () => continueCeoPaused(m.id) : undefined
          }
          continueLocked={busy}
        />
      );
    const atts = m.attachments ?? [];
    const mentions = m.agentMentions ?? [];
    if (!m.content && atts.length === 0 && mentions.length === 0) return null;
    // 异步团队收口：识别后隐藏（不渲染用户气泡，避免露出模型提示词）
    if (
      m.origin === "execution_harvest" ||
      (typeof m.content === "string" && m.content.startsWith("【系统收口】"))
    ) {
      return null;
    }
    return (
      <div key={m.id} className="bubble user">
        {m.content ? (
          <CollapsibleUserText contentKey={m.content}>
            {m.content}
          </CollapsibleUserText>
        ) : null}
        <UserBubbleChips attachments={atts} agentMentions={mentions} />
      </div>
    );
  }

  return (
    <div className="screen">
      <header className="bar">
        <button
          type="button"
          className="link icon-btn"
          aria-label="对话历史"
          onClick={() => setDrawerOpen(true)}
        >
          <Menu size={20} />
        </button>
        <span className="bar-title">{conversationId ? "对话" : "新对话"}</span>
        <div className="bar-right">
          {conversationId && (
            <button
              type="button"
              className="link icon-btn"
              aria-label={
                workspaceLabel
                  ? `打开「${workspaceLabel}」的文件`
                  : "本对话文件"
              }
              title={
                workspaceLabel
                  ? `打开「${workspaceLabel}」的文件`
                  : "本对话文件"
              }
              onClick={() => navigate(`/c/${conversationId}/files`)}
            >
              <Folder size={20} />
            </button>
          )}
          <button
            type="button"
            className="link icon-btn"
            aria-label="新对话"
            onClick={() => navigate("/", { state: {} })}
          >
            <SquarePen size={20} />
          </button>
        </div>
      </header>

      <div className="messages-pane">
        <div className="messages" ref={scrollRef}>
          {history === null && !error && <p className="muted hint">加载中…</p>}
          {history !== null &&
            history.length === 0 &&
            turns.length === 0 &&
            !creatingConversation &&
            !error &&
            (() => {
              // 平台代付、开箱即用：无「先接入模型」门，keyless 直接进欢迎态。
              if (conversationId) {
                return <p className="muted hint">发一条消息开始对话。</p>;
              }
              const copy = emptyChatCopy();
              return (
                <div className="chat-welcome">
                  <div className="chat-welcome-title">{copy.title}</div>
                  <div className="chat-welcome-sub">{copy.subtitle}</div>
                </div>
              );
            })()}
          {hasMoreBefore && (
            <button
              type="button"
              className="load-older"
              onClick={() => void loadOlder()}
              disabled={loadingOlder}
            >
              {loadingOlder ? "加载中…" : "加载更早的消息"}
            </button>
          )}
          {history?.map((m, i) => {
            const row = renderHistoryRow(
              m,
              i === history.length - 1 && turns.length === 0,
            );
            const anchored = memoryAnchors.before.get(m.id);
            if (!anchored) return row;
            return (
              <Fragment key={`anchor-${m.id}`}>
                <MemoryUpdateCard updates={anchored} />
                {row}
              </Fragment>
            );
          })}
          {turns.map((turn) => {
            const isLiveStream =
              busy &&
              activeStreamTurnId != null &&
              turn.id === activeStreamTurnId;
            const showAssistant =
              turn.events.length > 0 || isLiveStream || turn.userText === null;
            return (
              <div key={turn.id} className="turn">
                <UserTurnBubble turn={turn} />
                {showAssistant ? (
                  <AssistantBubble
                    turn={turn}
                    live={isLiveStream}
                    conversationId={conversationId ?? null}
                    onFill={fillComposer}
                    onOpenBrowserLive={
                      conversationId ? openBrowserLive : undefined
                    }
                    onContinue={
                      conversationId
                        ? () => {
                            const mid = extractMessageId(turn.events);
                            if (!mid) return;
                            return continueCeoPaused(mid);
                          }
                        : undefined
                    }
                    continueLocked={busy}
                  />
                ) : null}
              </div>
            );
          })}
          {/* 建会话在途的那份草稿：先摆气泡再等 POST，按下发送立刻看得见自己发了什么。 */}
          {draftPending && (
            <div className="turn" data-testid="draft-pending-turn">
              {draftPending.text ? (
                <div className="bubble user">
                  <CollapsibleUserText contentKey={draftPending.text}>
                    {draftPending.text}
                  </CollapsibleUserText>
                  <UserBubbleChips
                    attachments={draftPending.attachments}
                    agentMentions={draftPending.agentMentions}
                  />
                </div>
              ) : (
                <div className="bubble user">
                  <UserBubbleChips
                    attachments={draftPending.attachments}
                    agentMentions={draftPending.agentMentions}
                  />
                </div>
              )}
            </div>
          )}
          {/* 锚不到更晚用户消息的记忆卡 (③ §1.6): 最近一轮的固化结果，留在线程尾部。卡自己
              会滤掉空更新，所以最常见的「没有」情况什么也不渲染。 */}
          <MemoryUpdateCard updates={memoryAnchors.tail} />
        </div>
        {!atBottom && (history?.length || turns.length) ? (
          <button
            type="button"
            className="jump-bottom"
            onClick={jumpToBottom}
            aria-label="回到底部"
          >
            <ArrowDown size={14} aria-hidden />
            回到底部
          </button>
        ) : null}
      </div>

      {/* 另一端点掉的卡就地收口成只读条（B2 · 验收 5）：直接消失会让人以为是自己点的。 */}
      <RemoteSettledCards conversationId={conversationId ?? null} />

      {approvalCards.map((pending) =>
        conversationId ? (
          <PauseCard
            key={pending.id}
            pending={pending}
            conversationId={conversationId}
            onResolved={() =>
              setRecoveredInteractions((prev) =>
                prev.filter((a) => a.id !== pending.id),
              )
            }
          />
        ) : null,
      )}

      {!busy &&
        conversationId &&
        escalationCards.map((card) => (
          <EscalationAnswer
            key={card.id}
            esc={card.esc}
            escalationId={card.id}
            conversationId={conversationId}
            runId={card.runId || undefined}
            onOpenLive={openBrowserLive}
            onResolved={() => {
              setRecoveredInteractions((prev) =>
                prev.filter((a) => a.id !== card.id),
              );
              // 放行可能唤醒一个回合——对话级订阅本就停在这条对话上会自动跟播，
              // 这里只补一次排队条对账（重开应用条空但队仍在 / 多端）。
              reconcileQueuedRef.current(conversationId);
            }}
          />
        ))}

      {/* Durable resume cards — live authority = cold Interaction pending + stamp;
          recovery `paused` is reopen shell. Not gated on !busy (stamp may land while
          stream still draining; sending unlocks via visibleResumes effect). */}
      {visibleResumes.map((p) => (
        <ResumeCard
          key={`${p.message_id}:${p.checkpoint_id}`}
          paused={p}
          onResume={(decision, note, selected, amendments) =>
            void resume(p.message_id, decision, note, selected, amendments)
          }
          onOpenLive={conversationId ? openBrowserLive : undefined}
        />
      ))}

      {!busy &&
        conversationId &&
        stageCards.map((card) => (
          <StageCard
            key={card.id}
            card={card}
            onResolve={async (args) => {
              await runStageCard(card.id, args);
            }}
          />
        ))}

      {error &&
        !(error.fromTurn && latestOwnsOutlet) &&
        !latestPauseSurface && (
          <div className={errorSurfaceClass("bar", !!error.action)}>
            <span>{error.text}</span>
            <div className="error-bar-actions">
              <SupportDiagnosticCopyButton
                ids={
                  error.supportIds ?? (conversationId ? { conversationId } : {})
                }
              />
              {error.action && (
                <button
                  type="button"
                  className="link config-action"
                  onClick={() => {
                    const href = error.action?.href;
                    if (!href) return;
                    setError(null);
                    navigate(href);
                  }}
                >
                  {error.action.label}
                </button>
              )}
              {error.reconnect && (
                <button
                  type="button"
                  className="link reconnect"
                  onClick={() => reconnect()}
                >
                  重连
                </button>
              )}
            </div>
          </div>
        )}

      {attachError && (
        <div className="error bar">
          <span>{attachError}</span>
        </div>
      )}

      {(attachments.length > 0 || agentMentions.length > 0) && (
        <div className="attach-tray">
          {agentMentions.map((a) => (
            <span key={a.id} className="attach-chip">
              <span aria-hidden>@</span>
              <span className="attach-chip-name">{a.role}</span>
              <button
                type="button"
                className="attach-chip-x"
                onClick={() =>
                  setAgentMentions((prev) => prev.filter((x) => x.id !== a.id))
                }
                aria-label="移除角色点名"
              >
                ×
              </button>
            </span>
          ))}
          {attachments.map((a) => (
            <span key={attachmentDraftKey(a)} className="attach-chip">
              <span aria-hidden>
                {a.kind === "conversation"
                  ? "对话"
                  : a.kind === "dir"
                    ? "文件夹"
                    : "📎"}
              </span>
              <span className="attach-chip-name">{a.name}</span>
              {a.truncated && <span className="attach-chip-trunc">已截断</span>}
              <button
                type="button"
                className="attach-chip-x"
                onClick={() => removeAttachment(attachmentDraftKey(a))}
                aria-label="移除附件"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {voice.error && (
        <div className="error bar">
          <span>{voice.error}</span>
          <button type="button" className="link" onClick={voice.dismissError}>
            知道了
          </button>
        </div>
      )}

      {queueDroppedHint && (
        <div
          className="composer-delivery-hint"
          data-testid="queue-dropped-hint"
          // biome-ignore lint/a11y/useSemanticElements: 内嵌「知道了」按钮，<output> 语义不符——保留 aria live 容器。
          role="status"
        >
          <span>{queueDroppedHint}</span>
          <button
            type="button"
            className="queue-link"
            onClick={() => setQueueDroppedHint(null)}
          >
            知道了
          </button>
        </div>
      )}

      <QueuedTurnsBar
        conversationId={conversationId ?? null}
        onCancelled={(entry) => applyQueueCancelLocal(entry)}
        onCancelFailed={(text) => setError({ text })}
      />
      {voice.isRecording && (
        <VoiceRecordingBar
          duration={voice.duration}
          interimText={voice.interimText}
          onCancel={voice.cancel}
        />
      )}

      {/* 生成中有草稿：插队入口收到行外轻链（对齐桌面 Ctrl+Enter，不挤主槽）。 */}
      {!creatingConversation && trailing.showSteerHint && (
        <div
          className="composer-delivery-hint"
          data-testid="composer-delivery-hint"
        >
          <span>发送将排队至下一回合</span>
          <button
            type="button"
            className="queue-link"
            onClick={() => void sendForcedSteer()}
            disabled={history === null || stopPhase === "stopping"}
            aria-label={interruptible ? "插话" : "插队"}
            title={
              interruptible ? "插话（插入当前回合）" : "插队（插入当前回合）"
            }
            data-testid="force-steer-btn"
          >
            插队
          </button>
        </div>
      )}

      {composerOutcomeHint ? (
        <div
          className="composer-delivery-hint"
          data-testid="composer-outcome-hint"
          aria-live="polite"
        >
          <span>{composerOutcomeHint}</span>
          <SupportDiagnosticCopyButton ids={latestOutlet.supportIds} />
        </div>
      ) : null}

      {!conversationId && !creatingConversation && (
        <DraftFolderChip
          value={draftFolder}
          onChange={(next) => {
            setDraftFolder(next);
            navigate(".", {
              replace: true,
              state: next
                ? {
                    draftFolderId: next.id,
                    draftFolderName: next.name,
                  }
                : {},
            });
          }}
        />
      )}

      <div className="composer">
        <input
          ref={attachInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => void onPickFiles(e)}
        />
        <button
          type="button"
          className="attach-btn"
          onClick={() => setMoreOpen(true)}
          disabled={composerLocked}
          aria-label="更多选项"
          aria-expanded={moreOpen}
          title="更多"
        >
          ＋
        </button>
        <textarea
          ref={composerInputRef}
          className="composer-input"
          rows={1}
          placeholder={history === null ? "加载中…" : "说点什么…"}
          value={input}
          disabled={composerLocked}
          onChange={(e) => {
            const next = e.target.value;
            setInput(next);
            mention.syncMention(next, e.target.selectionStart ?? next.length);
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            // IME 组合态（中文选词等）：Enter 确认候选，勿当发送。
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229)
              return;
            e.preventDefault();
            void onSubmit();
          }}
        />
        {/* 建会话在途：还没有 run 可停，主槽摆不可点的「发送中」，别拿 Stop 骗人。 */}
        {creatingConversation && (
          <button
            type="button"
            className="send-btn"
            disabled
            aria-label="发送中"
            title="发送中"
            data-testid="draft-sending-btn"
          >
            <Loader2 size={18} className="voice-spin" aria-hidden />
          </button>
        )}
        {/* 态敏主槽：空闲空草稿=麦；有字=发送；生成中=Stop（有草稿时主发=queue，行外插队）。 */}
        {!creatingConversation &&
          trailing.row.map((slot) => {
            if (slot === "send") {
              return (
                <button
                  key={slot}
                  type="button"
                  className="send-btn"
                  onClick={() => void onSubmit()}
                  disabled={history === null || !hasDraft}
                  aria-label="发送"
                  title="发送"
                >
                  <Send size={18} aria-hidden />
                </button>
              );
            }
            if (slot === "stop") {
              return (
                <button
                  key={slot}
                  type="button"
                  className={`stop${stopPhase === "stopping" ? " stopping" : ""}`}
                  onClick={stop}
                  aria-label={stopButtonLabel(stopPhase)}
                  title={stopButtonLabel(stopPhase)}
                  aria-busy={stopPhase === "stopping"}
                >
                  {stopPhase === "stopping" ? (
                    <Loader2 size={18} className="voice-spin" aria-hidden />
                  ) : (
                    <Square size={16} aria-hidden />
                  )}
                </button>
              );
            }
            return (
              <VoiceButton
                key={slot}
                state={voice.state}
                disabled={composerLocked}
                onClick={voice.toggle}
              />
            );
          })}
      </div>

      {moreOpen && (
        <ComposerMoreSheet
          modelLabel={modelLabel}
          modelPreset={modelIsPreset}
          permissionLabel={permissionLabel}
          disabled={history === null || busy}
          onClose={() => setMoreOpen(false)}
          onOpenModel={() => {
            setMoreOpen(false);
            setPickerOpen(true);
          }}
          onOpenPermission={() => {
            setMoreOpen(false);
            setPermissionSheetOpen(true);
          }}
          onOpenMention={() => {
            setMoreOpen(false);
            mention.openBrowse();
          }}
        />
      )}

      {mention.open && (
        <ComposerMentionSheet
          query={mention.query}
          showCategoryLevel={mention.showCategoryLevel}
          categories={mention.categories}
          items={mention.items}
          emptyHint={mention.emptyHint}
          focusedLabel={mention.focusedLabel}
          canGoBack={mention.canGoBack}
          loading={mention.loading}
          error={mention.error}
          disabled={history === null || busy}
          onQueryChange={mention.setQuery}
          onDrill={mention.drill}
          onBack={mention.back}
          onSelect={mention.selectItem}
          onPickAttach={mention.pickAttach}
          onClose={mention.close}
        />
      )}

      {pickerOpen && (
        <ModelPicker
          conversationProfileId={currentProfileId}
          onSelect={(id) => void onSelectProfile(id)}
          onClose={() => setPickerOpen(false)}
        />
      )}

      {permissionSheetOpen && (
        <PermissionAxesSheet
          conversationId={conversationId ?? null}
          axes={permissionAxes}
          disabled={history === null || busy}
          onAxesChange={(next) => {
            setPermissionAxes(next);
            if (!conversationId) setPermissionDraftTouched(true);
          }}
          onClose={() => setPermissionSheetOpen(false)}
          onError={(text) => setError({ text })}
        />
      )}

      <ConversationDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onOpen={() => setDrawerOpen(true)}
        activeId={conversationId}
      />

      {conversationId ? (
        <BrowserLiveSheet
          conversationId={conversationId}
          sessionId={browserLiveSessionId}
          open={browserLiveOpen}
          onClose={() => {
            setBrowserLiveOpen(false);
            setBrowserLiveSessionId(null);
          }}
        />
      ) : null}
    </div>
  );
}
