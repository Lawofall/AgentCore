import { PausedContinueSurface } from "@/components/chat/PausedContinueSurface";
import {
  graphProgress,
  workerProgress,
  workersAreTerminal,
} from "@/components/chat/teamSynthesisPhase";
import {
  deriveCaptainStatus,
  hasActiveRunningWorkers,
  resolveCaptainSinkId,
} from "@/components/graph/helpers";
import { Badge, Button, IconButton as UiIconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useRunningElapsed } from "@/hooks/useRunningElapsed";
import { useChatPaneId, useChatPaneSliceKey } from "@/lib/chatPane";
import { formatDuration, formatLiveElapsed } from "@/lib/format";
import {
  PARTIAL_STATUS_LABEL,
  arbitrateTurnOutcome,
  failedRunsFromFrames,
  isAttestedPauseContinue,
  parseTurnOutcomeKind,
} from "@/lib/turnOutcome";
import { continuePausedTurn } from "@/services/turns/continuePaused";
import {
  runtimeOf,
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
  ChevronUp,
  Loader2,
  Maximize2,
  MessagesSquare,
  Pause,
  Square,
} from "lucide-react";

/** Props every lifecycle strip shares: projection + strip controls. */
export interface StatusStripProps {
  execution: Execution;
  expanded: boolean;
  onToggle: () => void;
  onMaximize: () => void;
}

/** Workers still in flight while the turn is paused (ask_user / continue).
 * Pending-only (next wave queued, nothing spinning) keeps the static pause strip.
 * Captain running is the CEO turn itself — not a worker batch. */
function hasActiveRunningRuns(execution: Execution): boolean {
  return hasActiveRunningWorkers(execution.runs);
}

/** 至少一名队员，且都已离开进行中。空名单不算团队已结束。 */
function teamRosterSettled(execution: Execution): boolean {
  const workers = execution.runs.filter((r) => r.kind !== "captain");
  return workers.length > 0 && workersAreTerminal(execution);
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
 * Lifecycle icon + n/m + duration + fold / canvas.
 * Running duration ticks from frames[0].t via shared useRunningElapsed
 * (`freezeWhenStopped` on 停止中). Completed still uses elapsedMs(frames) span.
 * No talking titles; Stop lives
 * on the composer, not here.
 *
 * Terminal faces follow the turn arbitrator (`showStripFailure` /
 * `showStripStopped` / `showStripIdle`), not `switch execution.status`.
 * User-stop is not an error; rate-limit / partial must not paint「已停止」.
 * Empty interrupt (`send_next`) is idle chrome — verdict lives on the composer.
 * Partial + rate-limit keeps this scoreboard; why + 排查包 follow `showComposerHint`.
 * Team fail / partial 排查包 hangs on the bubble footer, not this strip.
 * Failure face is the same thin scoreboard (失败 + n/m + duration);
 * task brief / failure sentence live on the node and dock.
 * stopping：可见「停止中」、冻住用时。工人全终态且图已
 * cancelled、仲裁未判 partial/error/限流 → 已停止，不等气泡 finishReason。
 *
 * Paused while a worker is still running: keep the running chrome (scoreboard
 * only — no talking title). Confirmation lives on the decision card.
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
  const detached = Boolean(useActiveExecField((rt) => rt.executionDetached));
  const scopeId = useExecutionScope();
  const conversationId = useChatPaneId();
  const paneKey = useChatPaneSliceKey();
  const scopedAssistant = useConversationStore((s) => {
    if (!scopeId) return null;
    const messages = runtimeOf(s, paneKey).messages;
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
  if (turnOutcome.kind === "partial") {
    return <PartialStrip {...props} />;
  }
  if (turnOutcome.kind === "paused") {
    if (hasActiveRunningRuns(props.execution)) {
      return <RunningStrip {...props} />;
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
    return <FailureStrip {...props} />;
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
  // 队员都结束了就收成团队完成条。CEO 还在写结尾不让条继续转。
  // 已 detached：人齐了仍走后台，直到 execution_completed。
  if (!detached && teamRosterSettled(props.execution)) {
    return <CompletedStrip {...props} />;
  }
  if (!detached && canPaintTeamCompleted(props.execution)) {
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

function StripControls({
  execution,
  expanded,
  onToggle,
  onMaximize,
}: StatusStripProps) {
  const debate = isDebate(execution);

  return (
    <>
      <StripIconButton
        icon={expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        title={expanded ? "收起协作图" : "展开协作图"}
        onClick={onToggle}
      />
      {/* 入口：辩论回合给醒目「打开辩论室」CTA，其余给通用「在画布打开」；
          二者同去处（放大态 Route A），辩论默认落群聊。 */}
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
  backgroundBadge,
}: StatusStripProps & { backgroundBadge?: boolean }) {
  const turnPhase = useActiveTurnPhase();
  const stopping = turnPhase === "stopping";
  const coordinationWait = useActiveExecField((rt) => rt.coordinationWait);
  // Background (detached): follow live execution.progress, not a frozen wait stamp.
  // stopping/terminal drop coordination_wait, so the pre-detach stamp never moves.
  const liveWait = backgroundBadge || stopping ? null : coordinationWait;
  const workers = workerProgress(execution);
  const graph = graphProgress(execution);
  const progressLabel = liveWait
    ? `${workers.completed}/${workers.total}`
    : `${graph.completed}/${graph.total}`;
  const frames = useActiveExecField((rt) => rt.frames);
  const elapsedSec = useRunningElapsed(!stopping, frames[0]?.t, {
    freezeWhenStopped: true,
  });
  const duration = formatLiveElapsed(elapsedSec) ?? "";
  const testId = stopping
    ? "status-strip-stopping"
    : backgroundBadge
      ? "status-strip-background"
      : liveWait
        ? "status-strip-coordination-wait"
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
        />
      </div>
    </div>
  );
}

/**
 * Mid-turn pause (e.g. ask_user gate) while the graph stays visible.
 * Static — no spinner — so pause is not painted as「正在协作 / 卡住」。
 */
function PausedStrip({
  execution,
  expanded,
  onToggle,
  onMaximize,
  continueAction,
}: StatusStripProps & {
  continueAction?: {
    reason: string | null;
    retryAfterSec?: number | null;
    onContinue: () => void;
  } | null;
}) {
  const { completed, total } = graphProgress(execution);

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
        />
      </div>
    </div>
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
}: StatusStripProps) {
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = graphProgress(execution);
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
}: StatusStripProps & { stopped?: boolean }) {
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = graphProgress(execution);
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
}: StatusStripProps) {
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = graphProgress(execution);
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
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
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
}: StatusStripProps) {
  const detached = useActiveExecField((rt) => rt.executionDetached);
  const frames = useActiveExecField((rt) => rt.frames);
  const { completed, total } = graphProgress(execution);
  const ms = elapsedMs(frames);
  const duration = ms > 0 ? formatDuration(ms) : "";

  return (
    <div className="px-3 py-1.5" data-testid="status-strip-failed">
      <div className="flex items-center gap-2">
        <LifeIcon label="失败">
          <AlertTriangle size={14} className="text-destructive" />
        </LifeIcon>
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-sm text-foreground">
          <span className="font-medium">失败</span>
          {detached ? (
            <Badge
              tone="primary"
              pill
              className="font-medium"
              data-testid="status-strip-failed-detached"
            >
              后台
            </Badge>
          ) : null}
          {isDebate(execution) && <DebateTag />}
          <span className="text-muted-foreground">
            {`${completed}/${total}${duration ? ` · 用时 ${duration}` : ""}`}
          </span>
        </span>
        <StripControls
          execution={execution}
          expanded={expanded}
          onToggle={onToggle}
          onMaximize={onMaximize}
        />
      </div>
    </div>
  );
}
