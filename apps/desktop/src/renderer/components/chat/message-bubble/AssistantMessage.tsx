import { FileArtifactsCard } from "@/components/chat/FileArtifactsCard";
import { Markdown } from "@/components/chat/Markdown";
import { PausedContinueSurface } from "@/components/chat/PausedContinueSurface";
import { SourceCards } from "@/components/chat/SourceCards";
import { TurnWarningBanner } from "@/components/chat/TurnWarningBanner";
import { CollapsibleSpeech } from "@/components/chat/debate/CollapsibleSpeech";
import { isAskSilentResolvedDecision } from "@/components/chat/decision";
import { Button, IconButton } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FinishReasonChip } from "@/components/ui/finish-reason-chip";
import {
  noticeChipNeutral,
  statusAccentText,
  statusChip,
} from "@/components/ui/tone-presets";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { buildCitationDisplayMap } from "@/lib/citationDisplayMap";
import { copyText } from "@/lib/clipboard";
import {
  TURN_CANCELLED_EMPTY_MESSAGE,
  connectivityEscalationSuffix,
  degradedFinishChipLabel,
  formatAssistantErrorMessage,
} from "@/lib/errors";
import { resolveFileArtifactsForCard } from "@/lib/fileArtifacts";
import {
  COST_UNPRICED_LABEL,
  formatCostCaption,
  pickCostMoney,
} from "@/lib/format";
import { formatMessageExport } from "@/lib/messageExport";
import {
  buildSupportDiagnosticPack,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportDiagnosticExtrasFromError,
} from "@/lib/supportDiagnostics";
import { notifySuccess } from "@/lib/toast";
import {
  assistantHasTeamStrip,
  isAttestedPauseContinue,
  turnOutcomeForAssistant,
} from "@/lib/turnOutcome";
import { cn } from "@/lib/utils";
import { runRegenerate } from "@/services/turns";
import { continuePausedTurn } from "@/services/turns/continuePaused";
import {
  assistantProjectionId,
  getActiveRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { useMessageInteractionCards } from "@/stores/interactions";
import { useUsageStore } from "@/stores/usage";
import { AlertTriangle, Check, Copy, KeyRound, RotateCcw } from "lucide-react";
import { useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { AssistantMessageFooter } from "./AssistantMessageFooter";
import { ComposingToolLine, ProcessTimeline } from "./ProcessTimeline";
import { SyncStatusHint } from "./SyncStatusHint";
import { ThinkingDots, ThinkingPanel } from "./Thinking";
import { UnproductiveToolFailureHint } from "./UnproductiveToolFailureHint";
import { WholeFilePasteHint } from "./WholeFilePasteHint";
import type { MessageBubbleProps } from "./types";
import { useCopyAction } from "./useCopyAction";

/** 长回答折叠阈值（px）：远高于用户气泡，只夹真正超长的答案。 */
const ANSWER_COLLAPSED_MAX_H = 640;

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
      className="mb-1.5 inline-flex items-center gap-1.5 px-2 py-0.5 font-normal"
    >
      <RotateCcw size={14} />
      曾中断恢复
    </Badge>
  );
}

/**
 * 回合产出文件，挂在答复正文之后。
 *
 * 交付对账（同 execution_id 保最新）→ 产物清单；可用性短问可在无 plan 的 CEO 回合复用
 * delivery_status，所以单 / 多 Agent 走同一条路径。产出卡只列文件；裸聊自动建桌的落点
 * 不在对话里告知（文件夹进「我的文件」，改名走文件页）。
 */
function TurnFiles({
  messageId,
  conversationId,
}: {
  messageId: string;
  conversationId: string | null;
}) {
  const deliveryStatus = useExecutionStore(
    (s) => s.byId[messageId]?.deliveryStatus ?? null,
  );
  const artifacts = useMemo(
    () => resolveFileArtifactsForCard(deliveryStatus),
    [deliveryStatus],
  );
  if (artifacts.length === 0) return null;
  return (
    <FileArtifactsCard
      artifacts={artifacts}
      conversationId={conversationId}
      turnKey={messageId}
    />
  );
}

export function AssistantMessage({ message }: MessageBubbleProps) {
  const loadMessageCost = useUsageStore((s) => s.loadMessageCost);
  const cachedTurn = useUsageStore((s) => s.messageCosts[message.id] ?? null);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const waitingForWorkspaceLock = useConversationStore((s) => {
    const id = s.currentConversationId;
    if (!id) return false;
    return s.byId?.[id]?.waitingForWorkspaceLock ?? false;
  });
  const navigate = useNavigate();
  const finishReason = !message.isStreaming
    ? (message.finishReason ?? message.runs?.finishReason)
    : undefined;
  // Execution / graph slot key = server turn id when stamped (pause/resume share it).
  // ALSO the interaction lookup key: SSE / journal hydration writes interaction
  // entries keyed by `serverMessageId ?? id` (execMessageId), so the query MUST use
  // the same projection key — querying by the local client UUID silently missed
  // every card (统一投影键, 时间线一期).
  const projectionId = assistantProjectionId(message);
  const { checkpoints, planReviews, teamPreviews } = useMessageInteractionCards(
    conversationId,
    projectionId,
  );
  const hasDedicatedPauseOrAskUi =
    checkpoints.length > 0 || planReviews.length > 0 || teamPreviews.length > 0;
  const execSlot = useExecutionStore((s) => s.byId[projectionId]);
  const hasTeamStrip = assistantHasTeamStrip(message, execSlot);
  const outcome = turnOutcomeForAssistant(message, execSlot, {
    hasDedicatedPauseOrAskUi,
    hasTeamStrip,
    finishReason,
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
  const errorAction =
    outcome.recovery.kind === "configure" && outcome.recovery.href
      ? {
          label: outcome.recovery.label ?? "去服务商",
          href: outcome.recovery.href,
        }
      : null;
  const emptyDiagnosis = message.error?.context?.empty_diagnosis;
  const supportDiagnosticIds = {
    conversationId,
    messageId: assistantProjectionId(message),
    userMessageId: precedingUserMessageId(
      getActiveRuntime().messages,
      message.id,
    ),
    traceId: message.traceId,
    executionId: message.executionId,
    ...supportDiagnosticExtrasFromError(message.error),
  };
  const supportDiagnosticText =
    formatSupportDiagnosticText(supportDiagnosticIds);
  const copySupportDiagnostics = () => {
    if (!supportDiagnosticText) return;
    void buildSupportDiagnosticPack(supportDiagnosticIds).then((text) => {
      if (!text) return;
      void copyText(text).then((ok) => {
        if (ok) notifySuccess("已复制排查包");
      });
    });
  };
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
  // Display renumbering: append-only across stream frames so assigned numbers
  // never jump. Reset when the message identity changes (component remounts per
  // bubble; also guard via message.id in case of reuse).
  const prevDisplayRef = useRef<Map<number, number>>(new Map());
  const prevMessageIdRef = useRef(message.id);
  if (prevMessageIdRef.current !== message.id) {
    prevMessageIdRef.current = message.id;
    prevDisplayRef.current = new Map();
  }
  const citationDisplay = useMemo(() => {
    const next = buildCitationDisplayMap(
      message.content,
      citations.length,
      prevDisplayRef.current,
      citations,
    );
    prevDisplayRef.current = next.stableCited;
    return next;
  }, [message.content, citations]);
  // 仅「仍会画存根」的 resolved 才藏正文；取消静默（stop / research_first）否则会空泡。
  const hideContentForCheckpoint = checkpoints.some(
    (c) => c.status === "resolved" && !isAskSilentResolvedDecision(c.decision),
  );
  // absorb/content_reset 后 content 空、问句只在 checkpoint.question：静默 dismiss 时
  // display-time 回落为普通 Markdown（不写回 store）。
  const rawContent = message.content ?? "";
  const displayContent =
    rawContent.trim() || hideContentForCheckpoint
      ? rawContent
      : (checkpoints.find(
          (c) =>
            c.status === "resolved" &&
            isAskSilentResolvedDecision(c.decision) &&
            c.question.trim(),
        )?.question ?? rawContent);
  const money =
    pickCostMoney(message.cost) ??
    (cachedTurn
      ? pickCostMoney({
          total: cachedTurn.cost.total,
          currency: cachedTurn.cost.currency,
          estimated_total: cachedTurn.estimated_cost?.total ?? null,
          estimated_currency: cachedTurn.estimated_cost?.currency ?? null,
        })
      : null);
  // 未计价可见 (拍板 2026-07-20)：BYOK 无价可算时明示「未计价」，不静默省略。
  const costText =
    message.executionId === null && money != null && money.nano > 0
      ? formatCostCaption(money.nano, money.estimated, money.currency)
      : message.executionId === null &&
          message.cost?.pricing_source === "unpriced"
        ? COST_UNPRICED_LABEL
        : null;

  const onPeekCost = () => {
    if (!message.isStreaming && message.cost == null) {
      void loadMessageCost(message.id);
    }
  };

  // 流式中可复制 (对话基础功能补齐): full footer is gated on THIS message's isStreaming
  // (not session isGenerating — a settled bubble must keep regenerate/cost while another
  // turn streams). Mid-stream usage/regenerate are meaningless, but a long reply is often
  // worth copying early — lightweight copy while streaming. Default = 仅交付; with process
  // timeline offer「含过程」too.
  const exportError = { error: message.error, runs: message.runs };
  const { copied: streamCopied, onCopy: onStreamCopy } = useCopyAction(() =>
    formatMessageExport(
      message.content,
      message.process,
      "deliverable",
      exportError,
    ),
  );
  const { copied: streamCopiedProcess, onCopy: onStreamCopyProcess } =
    useCopyAction(() =>
      formatMessageExport(
        message.content,
        message.process,
        "with_process",
        exportError,
        message.isStreaming,
      ),
    );

  const handleRegenerate = () => {
    const userId = precedingUserMessageId(
      getActiveRuntime().messages,
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
  // 回合级附件（收到的上下文 / 错误卡 / 产物 / 引用 / 检查点 / 操作行）随后平铺。
  const turnBody = hasProcess ? (
    <ProcessTimeline
      process={message.process ?? []}
      isStreaming={message.isStreaming}
      citations={citations}
      citationToDisplay={citationDisplay.toDisplay}
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
      planReviews={planReviews}
      teamPreviews={teamPreviews}
    />
  ) : (
    <>
      {hasReasoning && (
        <ThinkingPanel
          reasoning={message.reasoning ?? ""}
          isStreaming={message.isStreaming}
          persistKey={`${message.id}:reasoning`}
        />
      )}
      {/* 不变量（时间线一期）：多 Agent 回合必有 `team` 标记（live 由
          setLastAssistantExecutionId 盖章，reload 由 journal 补齐）→ hasProcess 恒真、
          协作图只在 ProcessTimeline 的标记槽渲染；此分支仅剩单 Agent 纯文本回合。 */}
      {/* 长回答折叠 (对话基础功能补齐): while streaming, render full so the user watches
          it grow; once settled, cap a truly long answer to a fade + 展开全文 so it doesn't
          dominate the viewport (短/中答案原样全展). */}
      {message.isStreaming && !hideContentForCheckpoint ? (
        displayContent.trim() ? (
          <Markdown
            content={displayContent}
            citations={citations}
            citationToDisplay={citationDisplay.toDisplay}
            knownLedgerIds={knownLedgerIds}
            evidenceLedger={evidenceLedger}
            isStreaming={message.isStreaming}
          />
        ) : null
      ) : hideContentForCheckpoint || !displayContent.trim() ? null : (
        <CollapsibleSpeech
          contentKey={displayContent}
          fadeToClass="from-background"
          collapsedMaxH={ANSWER_COLLAPSED_MAX_H}
          sceneKey={`answer:${message.id}`}
        >
          <Markdown
            content={displayContent}
            citations={citations}
            citationToDisplay={citationDisplay.toDisplay}
            knownLedgerIds={knownLedgerIds}
            evidenceLedger={evidenceLedger}
            isStreaming={false}
          />
        </CollapsibleSpeech>
      )}
      {message.isStreaming &&
        (message.composingTool && message.executionId === null ? (
          <ComposingToolLine tool={message.composingTool} />
        ) : displayContent.length === 0 && !hasReasoning ? (
          <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <ThinkingDots />
            {/* 不得静默等锁：写锁短等用诚实等待态，禁空 Thinking… 冒充 */}
            {waitingForWorkspaceLock ? "等待工作区…" : "Thinking…"}
          </span>
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
      {outcome.showFinishReasonChip && (
        <FinishReasonChip
          reason={finishReason}
          diagnosisLabel={degradedFinishChipLabel(
            emptyDiagnosis,
            displayError?.message ?? message.error?.message,
          )}
        />
      )}
      {message.turnWarning &&
        message.turnWarning !== TURN_CANCELLED_EMPTY_MESSAGE && (
          <TurnWarningBanner message={message.turnWarning} />
        )}
      {turnBody}
      {!message.isStreaming && (
        <UnproductiveToolFailureHint
          finishReason={finishReason}
          content={message.content}
          process={message.process}
          journal={message.runs}
        />
      )}
      {!message.isStreaming && (
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
      {/* Tone: 去配置 action → primary；限流 / 无 action → noticeChipNeutral（非危险红）。 */}
      {outcome.showBubbleBanner && displayError && (
        <div
          className={cn(
            "mt-2 flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm",
            errorAction ? statusChip.primary : noticeChipNeutral,
          )}
        >
          <AlertTriangle
            size={15}
            className={cn(
              "mt-0.5 shrink-0",
              errorAction ? statusAccentText.primary : "text-muted-foreground",
            )}
          />
          <p className="min-w-0 flex-1 whitespace-pre-wrap break-words">
            {formatAssistantErrorMessage(displayError)}
            {connectivityEscalationSuffix(displayError.code, message.id, {
              message: displayError.message,
              upstreamStatus: message.error?.context?.upstream_status,
              emptyDiagnosis,
              conversationId,
            })}
          </p>
          {outcome.supportPackHost === "bubble" && supportDiagnosticText && (
            <Button
              variant="ghost"
              className={
                errorAction
                  ? "shrink-0 text-primary/70 hover:bg-transparent hover:text-primary"
                  : "shrink-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
              }
              icon={<Copy size={13} />}
              onClick={copySupportDiagnostics}
            >
              复制排查包
            </Button>
          )}
          {errorAction && (
            <Button
              variant="primary"
              className="shrink-0"
              icon={<KeyRound size={13} />}
              onClick={() => navigate(errorAction.href)}
            >
              {errorAction.label}
            </Button>
          )}
        </div>
      )}
      <TurnFiles messageId={projectionId} conversationId={conversationId} />
      {citations.length > 0 && (
        <SourceCards
          citations={citations}
          displayMap={citationDisplay}
          turnKey={projectionId}
          evidenceLedger={evidenceLedger}
        />
      )}
      {/* 底部堆叠回退已废除（时间线一期）：交互卡只在 ProcessTimeline 标记槽渲染。
          不变量「有交互卡必有时间线标记」由 live 盖章 + reload journal 补标记保证。 */}
      {message.isStreaming && message.content.length > 0 && (
        <div className="mt-1 flex items-center opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          {(message.process?.length ?? 0) > 0 ? (
            <DropdownMenu>
              <SimpleTooltip
                label={streamCopied || streamCopiedProcess ? "已复制" : "复制"}
              >
                <DropdownMenuTrigger asChild>
                  <IconButton size="sm" aria-label="复制">
                    {streamCopied || streamCopiedProcess ? (
                      <Check size={14} />
                    ) : (
                      <Copy size={14} />
                    )}
                  </IconButton>
                </DropdownMenuTrigger>
              </SimpleTooltip>
              <DropdownMenuContent align="start" className="min-w-40">
                <DropdownMenuItem onSelect={() => void onStreamCopy()}>
                  仅交付
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void onStreamCopyProcess()}>
                  含过程
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <SimpleTooltip label={streamCopied ? "已复制" : "复制"}>
              <IconButton
                size="sm"
                aria-label="复制"
                onClick={() => void onStreamCopy()}
              >
                {streamCopied ? <Check size={14} /> : <Copy size={14} />}
              </IconButton>
            </SimpleTooltip>
          )}
        </div>
      )}
      {!message.isStreaming && message.syncStatus && (
        <div className="mt-1">
          <SyncStatusHint syncStatus={message.syncStatus} />
        </div>
      )}
      {outcome.showFooter && (
        <AssistantMessageFooter
          message={message}
          captainContext={captainContext}
          costText={costText}
          finishReason={finishReason}
          onRegenerate={handleRegenerate}
          displayError={displayError}
        />
      )}
    </div>
  );
}
