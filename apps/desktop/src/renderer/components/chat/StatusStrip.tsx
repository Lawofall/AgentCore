import { PausedContinueSurface } from "@/components/chat/PausedContinueSurface";
import {
  isTeamSynthesizing,
  workerProgress,
  workersAreTerminal,
} from "@/components/chat/teamSynthesisPhase";
import { failureDetailSentence } from "@/components/graph/agentNode/shared";
import {
  deriveCaptainStatus,
  hasActiveRunningWorkers,
  resolveCaptainSinkId,
} from "@/components/graph/helpers";
import { Badge, Button, IconButton as UiIconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { copyText } from "@/lib/clipboard";
import { formatDuration } from "@/lib/format";
import { runningElapsedSec } from "@/lib/runningElapsed";
import {
  buildSupportDiagnosticPack,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportDiagnosticExtrasFromError,
} from "@/lib/supportDiagnostics";
import { notifySuccess } from "@/lib/toast";
import {
  PARTIAL_STATUS_LABEL,
  arbitrateTurnOutcome,
  failedRunsFromFrames,
  isAttestedPauseContinue,
  parseTurnOutcomeKind,
} from "@/lib/turnOutcome";
import { continuePausedTurn } from "@/services/turns/continuePaused";
import {
  getActiveRuntime,
  isTerminalPhase,
  useActiveError,
  useActiveTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import {
  type Execution,
  elapsedMs,
  isDebate,
  useActiveExecField,
  useExecutionScope,
} from "@/stores/execution";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Copy,
  Loader2,
  Maximize2,
  MessagesSquare,
  Pause,
  Play,
  Square,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** Props every lifecycle strip shares: projection + strip controls. */
export interface StatusStripProps {
  execution: Execution;
  expanded: boolean;
  onToggle: () => void;
  onMaximize: () => void;
  onReplay: () => void;
  /** Incremental kickoff: overlay「新批次待确认」on the running strip. */
  pendingBatchBadge?: boolean;
}

/** First batch still actively running (incremental kickoff overlay gate).
 * Pending-only (next wave queued, nothing spinning) keeps the static pause strip.
 * Captain running is the CEO turn itself — not a worker batch. */
function hasActiveRunningRuns(execution: Execution): boolean {
  return hasActiveRunningWorkers(execution.runs);
}

/** 工人未齐或汇聚点非 completed 时不得画「完成」（与 deriveCaptainStatus 一致）. */
function canPaintTeamCompleted(execution: Execution): boolean {
  const captainId = resolveCaptainSinkId(execution.runs);
  if (captainId) {
    return deriveCaptainStatus(execution, captainId) === "completed";
  }
  return workersAreTerminal(execution);
}

/**
 * Thin toolbar above the collaboration graph (前端UX设计.md §三 / 协作图 UX §三).
 * Lifecycle icon + n/m + duration + fold / canvas / replay.
 * Running duration ticks from frames[0].t (ToolLine useRunningElapsed shape);
 * completed still uses elapsedMs(frames) span. No talking titles; Stop lives
 * on the composer, not here.
 *
 * Terminal faces follow the turn arbitrator (`showStripFailure` /
 * `showStripStopped` / `showStripIdle`), not `switch execution.status`.
 * User-stop is not an error; rate-limit / partial must not paint「已停止」.
 * Empty interrupt (`send_next`) is idle chrome — verdict lives on the composer.
 * Partial + rate-limit keeps this scoreboard; why + 排查包 follow `showComposerHint`.
 * stopping：可见「停止中」、冻住用时、不挂回放 Play。工人全终态且图已
 * cancelled、仲裁未判 partial/error/限流 → 已停止，不等气泡 finishReason。
 *
 * Incremental kickoff (`paused` while first batch still running): keep the
 * running chrome and overlay a「新批次待确认」badge.
 */
export function StatusStrip(props: StatusStripProps) {
  const delivery = useActiveExecField((rt) => rt.deliveryStatus);
  const frames = useActiveExecField((rt) => rt.frames);
  const fromFrames = failedRunsFromFrames(frames);
  const fromExec = props.execution.runs
    .filter((r) => r.status === "failed")
    .map((r) => ({
      id: r.id,
      status: r.status,
      error: r.error,
      errorCode: r.errorCode ?? null,
      retryable: r.retryable ?? null,
      retryAfter: r.retryAfter ?? null,
      productLanded: r.productLanded ?? null,
    }));
  const runs = fromFrames.length > 0 ? fromFrames : fromExec;
  const attestedKind = useActiveExecField((rt) => rt.attestedOutcome);
  const scopeId = useExecutionScope();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const scopedAssistant = useConversationStore((s) => {
    const cid = s.currentConversationId;
    if (!cid || !scopeId) return null;
    const messages = s.byId?.[cid]?.messages;
    if (!messages) return null;
    return (
      messages.find(
        (m) =>
          m.role === "assistant" &&
          (m.id === scopeId || m.serverMessageId === scopeId),
      ) ?? null
    );
  });
  const runErr = runs.find((r) => r.errorCode || r.error);
  const sessionError = useActiveError();
  const turnOutcome = arbitrateTurnOutcome({
    attestedKind:
      parseTurnOutcomeKind(scopedAssistant?.outcome) ?? attestedKind,
    isStreaming: Boolean(scopedAssistant?.isStreaming),
    executionStatus: props.execution.status,
    deliveryState: delivery?.state ?? null,
    deliverySummary: delivery?.summary ?? null,
    runs,
    messageError: scopedAssistant?.error ?? null,
    runsError: runErr
      ? { code: runErr.errorCode, message: runErr.error }
      : null,
    usageError: scopedAssistant?.usage?.error ?? null,
    finishReason:
      scopedAssistant?.finishReason ??
      scopedAssistant?.runs?.finishReason ??
      null,
    conversationError: sessionError,
    content: scopedAssistant?.content,
    reasoning: scopedAssistant?.reasoning,
    processLength: scopedAssistant?.process?.length ?? 0,
    citationCount: scopedAssistant?.citations?.length ?? 0,
    turnWarning: Boolean(scopedAssistant?.turnWarning),
    hasTeamStrip: true,
    credentialSource:
      scopedAssistant?.error?.context?.credential_source ?? null,
  });
  const showSupportPack = turnOutcome.supportPackHost === "strip";
  if (turnOutcome.kind === "partial") {
    return <PartialStrip {...props} showSupportPack={showSupportPack} />;
  }
  if (turnOutcome.kind === "paused") {
    if (hasActiveRunningRuns(props.execution)) {
      return <RunningStrip {...props} pendingBatchBadge />;
    }
    return (
      <PausedStrip
        {...props}
        continueAction={
          isAttestedPauseContinue(turnOutcome) && conversationId && scopeId
            ? {
                reason: turnOutcome.message,
                retryAfterSec: turnOutcome.recovery.retryAfterSec ?? null,
                onContinue: () => {
                  void continuePausedTurn({
                    conversationId,
                    messageId: scopeId,
                  });
                },
              }
            : null
        }
      />
    );
  }
  if (turnOutcome.showStripFailure) {
    return (
      <FailureStrip
        {...props}
        verdictMessage={turnOutcome.message}
        sessionError={sessionError}
        showSupportPack={showSupportPack}
      />
    );
  }
  if (turnOutcome.showStripStopped) {
    return <CompletedStrip {...props} stopped />;
  }
  if (turnOutcome.showStripIdle) {
    return <IdleStrip {...props} />;
  }
  // Graph already cancelled + workers terminal: paint「已停止」without
  // waiting for bubble finishReason / message_end. kind!==ok keeps
  // rate-limit / partial / error on their existing faces.
  if (
    props.execution.status === "cancelled" &&
    workersAreTerminal(props.execution) &&
    turnOutcome.kind === "ok"
  ) {
    return <CompletedStrip {...props} stopped />;
  }
  if (
    canPaintTeamCompleted(props.execution) &&
    !isTeamSynthesizing(props.execution)
  ) {
    return <CompletedStrip {...props} />;
  }
  return <RunningOrBackgroundStrip {...props} />;
}

/** running：有 execution_detached → RunningStrip +「后台」徽标（活体 n/m / 转圈）。
 * 停止中优先走 RunningStrip，保留转圈过渡，不回退成后台。 */
function RunningOrBackgroundStrip(props: StatusStripProps) {
  const turnPhase = useActiveTurnPhase();
  const detached = useActiveExecField((rt) => rt.executionDetached);
  if (turnPhase === "stopping") {
    return <RunningStrip {...props} />;
  }
  if (detached) {
    return <RunningStrip {...props} backgroundBadge />;
  }
  return <RunningStrip {...props} />;
}

function DebateTag() {
  return (
    <Badge tone="primary" pill className="align-middle font-medium">
      辩论
    </Badge>
  );
}

function LifeIcon({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex shrink-0" role="img" aria-label={label}>
      {children}
    </span>
  );
}

function StripIconButton({
  icon,
  title,
  onClick,
  onContextMenu,
}: {
  icon: React.ReactNode;
  title: string;
  onClick: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}) {
  return (
    <SimpleTooltip label={title}>
      <UiIconButton
        type="button"
        onClick={onClick}
        onContextMenu={onContextMenu}
        aria-label={title}
      >
        {icon}
      </UiIconButton>
    </SimpleTooltip>
  );
}

/** Live wall-clock seconds since the first run frame (`frames[0].t`, epoch ms).
 * Same shape as ToolLine: 1s ticker only forces a re-render; the value is
 * recomputed from Date.now() each render so fold/remount does not reset.
 * Do not use elapsedMs(frames) here — that span freezes while a long tool
 * emits no frames (`: ping` is not a RunFrame).
 * `running=false` freezes the last wall-clock value (stopping) instead of
 * dropping the suffix or jumping to elapsedMs(frames). */
function useRunningElapsed(
  running: boolean,
  startedAt: number | null | undefined,
): number {
  const [, force] = useState(0);
  const frozenSec = useRef<number | null>(null);
  useEffect(() => {
    if (!running) return;
    frozenSec.current = null;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [running]);
  if (startedAt == null) return 0;
  if (!running) {
    if (frozenSec.current == null) {
      frozenSec.current = runningElapsedSec(startedAt);
    }
    return frozenSec.current;
  }
  return runningElapsedSec(startedAt);
}

function StripControls({
  execution,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
  hideReplay,
}: StatusStripProps & { hideReplay?: boolean }) {
  const canReplay =
    !hideReplay &&
    (execution.status === "completed" || execution.status === "cancelled");
  const debate = isDebate(execution);

  return (
    <>
      {canReplay && (
        <StripIconButton
          icon={<Play size={15} />}
          title="回放协作过程"
          onClick={onReplay}
        />
      )}
      <StripIconButton
        icon={expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        title={expanded ? "收起协作图" : "展开协作图"}
        onClick={onToggle}
      />
      {/* 入口：辩论回合给醒目「打开辩论室」CTA，其余给通用「在画布打开」；
          二者同去处（放大态 Route A），辩论默认落群聊、回放走同一去处 + 自动播放。 */}
      <Button
        variant="ghost"
        className="ml-0.5 shrink-0 bg-primary/10 text-primary hover:bg-primary/20"
        icon={debate ? <MessagesSquare size={13} /> : <Maximize2 size={13} />}
        onClick={onMaximize}
      >
        {debate ? "打开辩论室" : "在画布打开"}
      </Button>
    </>
  );
}

function RunningStrip({
  execution,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
  pendingBatchBadge,
  backgroundBadge,
}: StatusStripProps & { backgroundBadge?: boolean }) {
  const turnPhase = useActiveTurnPhase();
  const stopping = turnPhase === "stopping";
  const coordinationWait = useActiveExecField((rt) => rt.coordinationWait);
  // Background (detached): follow live execution.progress, not a frozen wait stamp.
  // stopping/terminal drop coordination_wait, so the pre-detach stamp never moves.
  const liveWait = backgroundBadge || stopping ? null : coordinationWait;
  const synthesizing =
    !isDebate(execution) &&
    !liveWait &&
    isTeamSynthesizing(execution, {
      turnTerminal: isTerminalPhase(turnPhase),
    });
  const workers = workerProgress(execution);
  const { completed, total } = execution.progress;
  const progressLabel = liveWait
    ? `${liveWait.completed}/${liveWait.total}`
    : synthesizing
      ? `${workers.completed}/${workers.total}`
      : `${completed}/${total}`;
  const frames = useActiveExecField((rt) => rt.frames);
  const elapsedSec = useRunningElapsed(!stopping, frames[0]?.t);
  const duration = elapsedSec > 0 ? formatDuration(elapsedSec * 1000) : "";
  const testId = stopping
    ? "status-strip-stopping"
    : pendingBatchBadge
      ? "status-strip-pending-batch"
      : backgroundBadge
        ? "status-strip-background"
        : liveWait
          ? "status-strip-coordination-wait"
          : synthesizing
            ? "status-strip-synthesizing"
            : undefined;

  return (
    <div className="px-3 py-1.5" data-testid={testId}>
      <div className="flex items-center gap-2">
        <LifeIcon
          label={stopping ? "停止中" : backgroundBadge ? "后台运行" : "进行中"}
        >
          <Loader2 size={14} className="animate-spin text-primary" />
        </LifeIcon>
        {stopping ? <span className="font-medium">停止中</span> : null}
        {isDebate(execution) && <DebateTag />}
        {pendingBatchBadge ? (
          <Badge
            tone="primary"
            pill
            className="shrink-0 font-medium"
            data-testid="status-strip-pending-batch-badge"
          >
            新批次待确认
          </Badge>
        ) : null}
        {backgroundBadge ? (
          <Badge
            tone="primary"
            pill
            className="shrink-0 font-medium"
            data-testid="status-strip-background-title"
          >
            后台
          </Badge>
        ) : null}
        <span className="min-w-0 flex-1" />
        <span className="shrink-0 text-xs text-muted-foreground">
          {`${progressLabel}${duration ? ` · 用时 ${duration}` : ""}`}
        </span>
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
          onReplay={onReplay}
          hideReplay={stopping}
        />
      </div>
    </div>
  );
}

/**
 * Mid-turn pause (e.g. plan_review / team_preview gate) while the graph stays visible.
 * Static — no spinner — so pause is not painted as「正在协作 / 卡住」。
 */
function PausedStrip({
  execution,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
  continueAction,
}: StatusStripProps & {
  continueAction?: {
    reason: string | null;
    retryAfterSec?: number | null;
    onContinue: () => void;
  } | null;
}) {
  const { completed, total } = execution.progress;

  return (
    <div className="px-3 py-1.5" data-testid="status-strip-paused">
      <div className="flex items-center gap-2">
        <LifeIcon label="已暂停">
          <Pause size={14} className="text-primary" />
        </LifeIcon>
        {isDebate(execution) && <DebateTag />}
        {continueAction ? (
          <PausedContinueSurface
            compact
            reason={continueAction.reason}
            retryAfterSec={continueAction.retryAfterSec}
            onContinue={continueAction.onContinue}
          />
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        <span className="shrink-0 text-xs text-muted-foreground">
          {completed}/{total}
        </span>
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
          onReplay={onReplay}
        />
      </div>
    </div>
  );
}

function StripSupportPack() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const scopeId = useExecutionScope();
  const scopedAssistant = useConversationStore((s) => {
    const cid = s.currentConversationId;
    if (!cid || !scopeId) return null;
    const messages = s.byId?.[cid]?.messages;
    if (!messages) return null;
    return (
      messages.find(
        (m) =>
          m.role === "assistant" &&
          (m.id === scopeId || m.serverMessageId === scopeId),
      ) ?? null
    );
  });
  const ids = {
    conversationId,
    messageId: scopeId ?? scopedAssistant?.id,
    userMessageId: scopedAssistant
      ? precedingUserMessageId(getActiveRuntime().messages, scopedAssistant.id)
      : null,
    traceId: scopedAssistant?.traceId,
    executionId: scopedAssistant?.executionId,
    ...supportDiagnosticExtrasFromError(scopedAssistant?.error),
  };
  const diagnosticText = formatSupportDiagnosticText(ids);
  if (!diagnosticText) return null;
  return (
    <Button
      variant="ghost"
      className="shrink-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
      icon={<Copy size={13} />}
      data-testid="status-strip-support-pack"
      onClick={() => {
        void buildSupportDiagnosticPack(ids).then((text) => {
          if (!text) return;
          void copyText(text).then((ok) => {
            if (ok) notifySuccess("已复制排查包");
          });
        });
      }}
    >
      复制排查包
    </Button>
  );
}

/**
 * Empty interrupt (`send_next`): n/m chrome only. Verdict lives on the composer.
 * No spinner, no「已停止」, no failure strip.
 */
function IdleStrip({
  execution,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
}: StatusStripProps) {
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = execution.progress;
  const ms = elapsedMs(frames);
  const duration = ms > 0 ? formatDuration(ms) : "";

  return (
    <div className="px-3 py-1.5" data-testid="status-strip-idle">
      <div className="flex items-center gap-2">
        {isDebate(execution) && <DebateTag />}
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-sm text-foreground">
          <span className="text-muted-foreground">
            {`${completed}/${total}${duration ? ` · 用时 ${duration}` : ""}`}
          </span>
        </span>
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
          onReplay={onReplay}
        />
      </div>
    </div>
  );
}

function CompletedStrip({
  execution,
  stopped,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
}: StatusStripProps & { stopped?: boolean }) {
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = execution.progress;
  const ms = elapsedMs(frames);
  const duration = ms > 0 ? formatDuration(ms) : "";

  // 子任务失败只靠 meta（n/m）+ 图节点色 + 右坞详情；完成/停止态不再挂红条复述。
  // 交付 unmet（partial/blocked）由气泡轻提示承担，完成态条保持中性勾。

  return (
    <div className="px-3 py-1.5">
      <div className="flex items-center gap-2">
        {stopped ? (
          <LifeIcon label="已停止">
            <Square size={14} className="text-muted-foreground" />
          </LifeIcon>
        ) : (
          <LifeIcon label="完成">
            <CheckCircle2 size={14} className="text-success" />
          </LifeIcon>
        )}
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-sm text-foreground">
          {stopped ? (
            <span className="font-medium">已停止</span>
          ) : (
            isDebate(execution) && <DebateTag />
          )}
          <span className="text-muted-foreground">
            {`${completed}/${total}${duration ? ` · 用时 ${duration}` : ""}`}
          </span>
        </span>
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
          onReplay={onReplay}
        />
      </div>
    </div>
  );
}

function PartialStrip({
  execution,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
  showSupportPack,
}: StatusStripProps & {
  showSupportPack: boolean;
}) {
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = execution.progress;
  const ms = elapsedMs(frames);
  const duration = ms > 0 ? formatDuration(ms) : "";

  return (
    <div className="px-3 py-1.5" data-testid="status-strip-partial">
      <div className="flex items-center gap-2">
        <LifeIcon label={PARTIAL_STATUS_LABEL}>
          <CheckCircle2 size={14} className="text-muted-foreground" />
        </LifeIcon>
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-sm text-foreground">
          <span className="font-medium">{PARTIAL_STATUS_LABEL}</span>
          <span className="text-muted-foreground">
            {`${completed}/${total}${duration ? ` · 用时 ${duration}` : ""}`}
          </span>
        </span>
        {showSupportPack ? <StripSupportPack /> : null}
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
          onReplay={onReplay}
        />
      </div>
    </div>
  );
}

function FailureStrip({
  execution,
  expanded,
  onToggle,
  onMaximize,
  onReplay,
  verdictMessage,
  sessionError,
  showSupportPack,
}: StatusStripProps & {
  verdictMessage: string | null;
  sessionError: string | null;
  showSupportPack: boolean;
}) {
  const detached = useActiveExecField((rt) => rt.executionDetached);
  // Long task briefs (e.g. code_audit instructions) must not explode the strip —
  // default clamp; click to expand.
  const [detailOpen, setDetailOpen] = useState(false);

  const failedRun = execution.runs.find((s) => s.status === "failed") ?? null;
  const failedAgent = failedRun
    ? (execution.agents.find((a) => a.id === failedRun.agentId) ?? null)
    : null;

  // Prefer the failed run, curated by failureKind — `run.error` is model-facing
  // (`str(exception)` / engine gate names) and reading it as advice sends users hunting
  // for material they never owed. Else the arbitrator verdict (same sentence as the
  // bubble / session banner). `run.error` is never the user face.
  const errorDetail = failedRun
    ? failureDetailSentence(failedRun.failureKind, failedRun.productLanded)
    : verdictMessage?.trim() ||
      sessionError?.trim() ||
      "未获取到具体错误信息。";

  const taskText = failedRun?.task?.trim() ?? "";
  const canToggleDetail =
    taskText.length > 72 ||
    errorDetail.length > 96 ||
    errorDetail.includes("\n");

  return (
    <div className="px-3 py-1.5" data-testid="status-strip-failed">
      {detached ? (
        <div
          className="mb-1.5 flex items-center gap-2 text-xs text-foreground"
          data-testid="status-strip-failed-detached"
        >
          <Pause size={13} className="shrink-0 text-primary" aria-hidden />
          <Badge tone="primary" pill className="font-medium">
            后台
          </Badge>
          <span className="text-muted-foreground">
            {execution.progress.completed}/{execution.progress.total}
          </span>
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <LifeIcon label="失败">
          <AlertTriangle size={14} className="text-destructive" />
        </LifeIcon>
        <span className="flex-1 text-sm text-foreground">
          <span className="font-medium">失败</span>
        </span>
        {showSupportPack ? <StripSupportPack /> : null}
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
          onReplay={onReplay}
        />
      </div>

      <div className="mt-1.5 rounded-lg bg-muted/40 px-3 py-2 text-sm">
        {canToggleDetail ? (
          <button
            type="button"
            onClick={() => setDetailOpen((v) => !v)}
            aria-expanded={detailOpen}
            aria-label={detailOpen ? "收起失败详情" : "展开失败详情"}
            data-testid="status-strip-failed-detail-toggle"
            className="flex w-full items-start gap-1.5 text-left"
          >
            <div className="min-w-0 flex-1">
              {failedAgent || failedRun ? (
                <p
                  className={
                    detailOpen
                      ? "whitespace-pre-wrap break-words text-foreground"
                      : "line-clamp-2 text-foreground"
                  }
                >
                  {failedAgent && (
                    <span className="font-medium">{failedAgent.role}</span>
                  )}
                  {taskText ? (
                    <span className="text-muted-foreground"> · {taskText}</span>
                  ) : null}
                </p>
              ) : (
                <p className="text-foreground">执行过程中出现错误</p>
              )}
              <p
                className={
                  detailOpen
                    ? "mt-1 whitespace-pre-wrap break-words text-xs text-destructive"
                    : "mt-1 line-clamp-2 break-words text-xs text-destructive"
                }
              >
                {errorDetail}
              </p>
            </div>
            <ChevronRight
              size={14}
              className={`mt-0.5 shrink-0 text-muted-foreground transition-transform ${
                detailOpen ? "rotate-90" : ""
              }`}
              aria-hidden
            />
          </button>
        ) : (
          <>
            {failedAgent || failedRun ? (
              <p className="text-foreground">
                {failedAgent && (
                  <span className="font-medium">{failedAgent.role}</span>
                )}
                {taskText ? (
                  <span className="text-muted-foreground"> · {taskText}</span>
                ) : null}
              </p>
            ) : (
              <p className="text-foreground">执行过程中出现错误</p>
            )}
            <p className="mt-1 whitespace-pre-wrap break-words text-xs text-destructive">
              {errorDetail}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
