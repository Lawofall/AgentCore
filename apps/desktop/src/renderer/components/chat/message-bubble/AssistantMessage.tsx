import { Markdown } from "@/components/chat/Markdown";
import { PausedContinueSurface } from "@/components/chat/PausedContinueSurface";
import { TurnWarningBanner } from "@/components/chat/TurnWarningBanner";
import { Badge } from "@/components/ui/badge";
import { useChatPaneId, useChatPaneSliceKey } from "@/lib/chatPane";
import { type SpendRun, resolveReplyInvoice } from "@/lib/cost";
import { formatDisplayCost } from "@/lib/format";
import { completedAtIso } from "@/lib/runningElapsed";
import { precedingUserMessageId } from "@/lib/supportDiagnostics";
import {
  assistantHasTeamStrip,
  isAttestedPauseContinue,
  turnOutcomeForAssistant,
} from "@/lib/turnOutcome";
import { cn } from "@/lib/utils";
import { runRegenerate } from "@/services/turns";
import { continuePausedTurn } from "@/services/turns/continuePaused";
import {
  type CheckpointDisplay,
  assistantProjectionId,
  getRuntime,
  runtimeOf,
  useConversationStore,
  useLiveTailWriting,
} from "@/stores/conversation";
import {
  type AgentState,
  type Execution,
  type RunNode,
  useExecutionStore,
  useMessageExecution,
} from "@/stores/execution";
import { useMessageInteractionCards } from "@/stores/interactions";
import { useUsageStore } from "@/stores/usage";
import { RotateCcw } from "lucide-react";
import { useMemo } from "react";
import {
  AssistantMessageFooter,
  AssistantMessageMetaSummary,
  AssistantTurnInspect,
} from "./AssistantMessageFooter";
import { LiveWaitLabel } from "./LiveFlow";
import { MessageTime } from "./MessageActions";
import { ComposingToolLine, ProcessTimeline } from "./ProcessTimeline";
import { SyncStatusHint } from "./SyncStatusHint";
import { ThinkingPanel } from "./Thinking";
import { WholeFilePasteHint } from "./WholeFilePasteHint";
import type { MessageBubbleProps } from "./types";

function spendLabel(run: RunNode, agents: AgentState[]): string {
  const titled = agents.find((agent) => agent.id === run.agentId)?.role.trim();
  if (titled) return titled;
  return run.role === "captain" ? "队长" : "队员";
}

function invoiceRuns(execution: Execution): SpendRun[] {
  return execution.runs.map((run) => ({
    id: run.id,
    status: run.status,
    role: run.role,
    label: spendLabel(run, execution.agents),
    cost: run.cost,
    usage: run.usage,
  }));
}

/**
 * 「曾中断恢复」：这条回合中途崩过、由系统重驱跑完，成果仍在本条消息里。
 * 诚实优先——不许静默假装一次跑完，所以标记常驻气泡顶部而非只进 footer。
 */
function RecoveredChip() {
  return (
    <Badge
      tone="muted"
      pill
      title="本回合中途中断，系统已自动接着跑完；成果就在这条消息里。"
      className="mb-2 inline-flex items-center gap-1.5 px-2 py-0.5 font-normal"
    >
      <RotateCcw size={14} />
      曾中断恢复
    </Badge>
  );
}

function askDuplicateStems(
  checkpoint: Pick<CheckpointDisplay, "question" | "questions">,
): string[] {
  const stems = (checkpoint.questions ?? [])
    .map((q) => q.prompt.trim())
    .filter(Boolean);
  const wire = checkpoint.question.trim();
  if (wire) stems.push(wire);
  return stems;
}

/** 问句已在结算存根里；正文只有「就是那句问句」时才藏，续聊必须露出。 */
function shouldHideAskDuplicateQuestion(
  checkpoints: readonly Pick<
    CheckpointDisplay,
    "status" | "question" | "questions"
  >[],
  content: string,
): boolean {
  const resolved = checkpoints.filter((c) => c.status === "resolved");
  if (resolved.length === 0) return false;
  const trimmed = content.trim();
  if (!trimmed) return true;
  return resolved.some((c) => askDuplicateStems(c).includes(trimmed));
}

export function AssistantMessage({ message }: MessageBubbleProps) {
  const loadMessageCost = useUsageStore((s) => s.loadMessageCost);
  const cachedTurn = useUsageStore((s) => s.messageCosts[message.id] ?? null);
  const conversationId = useChatPaneId();
  const paneKey = useChatPaneSliceKey();
  const liveTailWriting = useLiveTailWriting(message.id);
  const bubbleLive = message.isStreaming || liveTailWriting;
  const waitingForWorkspaceLock = useConversationStore(
    (s) => runtimeOf(s, paneKey).waitingForWorkspaceLock,
  );
  const waitingForDeskProvision = useConversationStore(
    (s) => runtimeOf(s, paneKey).waitingForDeskProvision,
  );
  const finishReason = !bubbleLive
    ? (message.finishReason ?? message.runs?.finishReason)
    : undefined;
  // Execution / graph slot key = server turn id when stamped (pause/resume share it).
  // ALSO the interaction lookup key: SSE / journal hydration writes interaction
  // entries keyed by `serverMessageId ?? id` (execMessageId), so the query MUST use
  // the same projection key — querying by the local client UUID silently missed
  // every card (统一投影键, 时间线一期).
  const projectionId = assistantProjectionId(message);
  const { checkpoints } = useMessageInteractionCards(
    conversationId,
    projectionId,
  );
  const hasDedicatedPauseOrAskUi = checkpoints.some(
    (c) => c.status === "pending",
  );
  const execSlot = useExecutionStore((s) => s.byId[projectionId]);
  const hasTeamStrip = assistantHasTeamStrip(message, execSlot);
  const outcome = turnOutcomeForAssistant(message, execSlot, {
    hasDedicatedPauseOrAskUi,
    hasTeamStrip,
    finishReason,
    isStreaming: bubbleLive,
  });
  // Prefer live message.error when it is the face source so context (upstream
  // preview / credential_source / empty_diagnosis) survives formatAssistantErrorMessage.
  const resolvedFace = outcome.face;
  const displayError =
    resolvedFace == null
      ? null
      : message.error &&
          (message.error.message?.trim() === resolvedFace.message ||
            message.error.code === resolvedFace.code)
        ? message.error
        : resolvedFace;
  const packPinned = outcome.supportPackHost === "more";
  const hasReasoning =
    !!message.reasoning && message.reasoning.trim().length > 0;
  const captainContext = message.captainContext ?? [];
  const hasProcess = (message.process?.length ?? 0) > 0;
  const citations = useMemo(() => message.citations ?? [], [message.citations]);
  const evidenceLedger = useMemo(
    () => message.evidenceLedger ?? [],
    [message.evidenceLedger],
  );
  const knownLedgerIds = useMemo(() => {
    const ids = new Set<string>();
    for (const e of evidenceLedger) {
      if (e.id) ids.add(e.id);
    }
    for (const c of citations) {
      if (c.id) ids.add(c.id);
    }
    return ids;
  }, [evidenceLedger, citations]);
  // 结算存根已画问句：正文只在「就是那句问句副本」时藏，避免贴在结论文旁像还在催。
  // CEO 续聊（确认/取消后的回复）必须露出。空 content 不回落问句——问句在存根展开里。
  const rawContent = message.content ?? "";
  const hideContentForCheckpoint = shouldHideAskDuplicateQuestion(
    checkpoints,
    rawContent,
  );
  const displayContent = rawContent;
  const hasTeamGraph = message.executionId != null;
  const execution = useMessageExecution(hasTeamGraph ? projectionId : null);
  const ledger =
    message.cost ??
    (cachedTurn
      ? {
          total: cachedTurn.cost.total,
          currency: cachedTurn.cost.currency,
          estimated_total: cachedTurn.estimated_cost?.total ?? null,
          estimated_currency: cachedTurn.estimated_cost?.currency ?? null,
        }
      : null);
  const invoice = resolveReplyInvoice({
    ledger,
    runs: execution ? invoiceRuns(execution) : [],
    soloAccrued: message.accruedCost,
    soloUsage: message.accruedUsage,
    bubbleLive,
  });
  const costText =
    invoice.money != null
      ? `${formatDisplayCost(
          invoice.money.nano,
          invoice.money.estimated,
          invoice.money.currency,
        )}${invoice.provisional ? " 至今" : ""}`
      : null;
  const showDuration =
    !bubbleLive && message.durationMs != null && message.durationMs > 0;
  const showCostMeta = costText != null || showDuration;

  const onPeekCost = () => {
    if (!bubbleLive && message.cost == null) {
      void loadMessageCost(message.id);
    }
  };

  const handleRegenerate = () => {
    const userId = precedingUserMessageId(
      getRuntime(paneKey).messages,
      message.id,
    );
    if (userId) void runRegenerate(userId);
  };

  // Empty user-stop: MessageBubble is the list-level omit (hideEmptyBubble).
  // Keep this short path so direct renders (tests) stay clean — not a second verdict.
  if (outcome.hideEmptyBubble) {
    return null;
  }

  // 回合正文（时间线或答案）：对话页恒为传统聊天平铺（单 Agent 回合不再退化成 CEO 节点卡——
  // 那条「图主界面化」第一刀已撤，图相关体验只在画布；多 Agent 回合协作图内嵌在
  // `team` 标记槽——CEO 导语 content 步之下（协作图时间线落点））。
  // 回合级附件（收到的上下文 / 产物 / 引用 / 检查点 / 操作行）随后平铺。失败说明在输入区横幅。
  const turnBody = hasProcess ? (
    <ProcessTimeline
      process={message.process ?? []}
      isStreaming={bubbleLive}
      citations={citations}
      knownLedgerIds={knownLedgerIds}
      evidenceLedger={evidenceLedger}
      composingTool={
        message.executionId === null ? (message.composingTool ?? null) : null
      }
      fallbackContent={hideContentForCheckpoint ? "" : displayContent}
      messageId={projectionId}
      journal={message.runs}
      conversationId={conversationId}
      checkpoints={checkpoints}
    />
  ) : (
    <>
      {hasReasoning && (
        <ThinkingPanel
          reasoning={message.reasoning ?? ""}
          isStreaming={bubbleLive}
          persistKey={`${message.id}:reasoning`}
        />
      )}
      {/* 不变量（时间线一期）：多 Agent 回合必有 `team` 标记（live 由
          setLastAssistantExecutionId 盖章，reload 由 journal 补齐）→ hasProcess 恒真、
          协作图只在 ProcessTimeline 的标记槽渲染；此分支仅剩单 Agent 纯文本回合。 */}
      {/* 终稿全文：思考/工具折进摘要，正文（含中间段）不夹「展开全文」。复盘扫读夹层只在 admin。 */}
      {hideContentForCheckpoint || !displayContent.trim() ? null : (
        <Markdown
          content={displayContent}
          conversationId={conversationId}
          citations={citations}
          knownLedgerIds={knownLedgerIds}
          evidenceLedger={evidenceLedger}
          isStreaming={bubbleLive}
        />
      )}
      {bubbleLive &&
        (message.composingTool && message.executionId === null ? (
          <ComposingToolLine tool={message.composingTool} />
        ) : displayContent.length === 0 && !hasReasoning ? (
          <LiveWaitLabel>
            {/* 不得静默等锁：写锁短等用诚实等待态，禁空 Thinking… 冒充 */}
            {waitingForWorkspaceLock
              ? "等待工作区…"
              : waitingForDeskProvision
                ? "正在准备云端环境"
                : "Thinking…"}
          </LiveWaitLabel>
        ) : (
          <span
            className="mt-1 inline-block h-4 w-1.5 rounded-full bg-foreground/60"
            style={{ animation: "blink-cursor 0.8s step-end infinite" }}
          />
        ))}
    </>
  );

  return (
    <div className="group min-w-0" onMouseEnter={onPeekCost}>
      {message.recovered && <RecoveredChip />}
      {outcome.showTurnWarning && message.turnWarning && (
        <TurnWarningBanner message={message.turnWarning} />
      )}
      {turnBody}
      {!bubbleLive && (
        <WholeFilePasteHint
          content={message.content}
          process={message.process}
          journal={message.runs}
        />
      )}
      {isAttestedPauseContinue(outcome) && !hasTeamStrip && conversationId && (
        <PausedContinueSurface
          reason={outcome.message}
          retryAfterSec={outcome.recovery.retryAfterSec}
          onContinue={() => {
            void continuePausedTurn({
              conversationId,
              messageId: projectionId,
            });
          }}
        />
      )}
      {/* 底部堆叠回退已废除（时间线一期）：交互卡只在 ProcessTimeline 标记槽渲染。
          不变量「有交互卡必有时间线标记」由 live 盖章 + reload journal 补标记保证。 */}
      {!bubbleLive && message.syncStatus && (
        <div className="mt-1">
          <SyncStatusHint syncStatus={message.syncStatus} />
        </div>
      )}
      {outcome.showFooter ? (
        <AssistantMessageFooter
          message={message}
          captainContext={captainContext}
          costText={costText}
          spendLines={invoice.lines}
          showDuration={showDuration}
          onRegenerate={handleRegenerate}
          displayError={displayError}
          pinSupportPack={packPinned}
          showRegenerate={outcome.showRegenerate}
        />
      ) : showCostMeta || packPinned ? (
        <div
          className={cn(
            "mt-1 flex items-center gap-2",
            packPinned ? "justify-between" : "justify-end",
          )}
        >
          {packPinned ? (
            <AssistantTurnInspect
              message={message}
              captainContext={captainContext}
              spendLines={invoice.lines}
            />
          ) : null}
          {showCostMeta ? (
            <div className="flex shrink-0 items-center gap-1.5">
              <AssistantMessageMetaSummary
                costText={costText}
                durationMs={showDuration ? message.durationMs : undefined}
              />
              <MessageTime
                iso={completedAtIso(message.createdAt, message.durationMs)}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
