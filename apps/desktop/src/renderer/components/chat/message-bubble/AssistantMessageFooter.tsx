import { ReceivedContextDialog } from "@/components/chat/ReceivedContext";
import { IconButton } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FINISH_REASON_META } from "@/components/ui/finish-reason-chip";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { copyText } from "@/lib/clipboard";
import { formatCompact, formatDuration } from "@/lib/format";
import { formatMessageExport } from "@/lib/messageExport";
import {
  buildSupportDiagnosticPack,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportDiagnosticExtrasFromError,
} from "@/lib/supportDiagnostics";
import { notifyError, notifySuccess } from "@/lib/toast";
import { setMessageFeedback } from "@/services/messages";
import type { UsageBreakdown } from "@/services/usage";
import { useBookmarkStore } from "@/stores/bookmarks";
import type { Message } from "@/stores/conversation";
import {
  assistantProjectionId,
  getActiveRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { turnDetailPath } from "@/stores/ui";
import type { ContextBlockWire } from "@/types/events";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheUsageDisplay,
} from "@agentcore/protocol-fold-kit";
import {
  Bookmark,
  Check,
  Copy,
  Fingerprint,
  Layers,
  Link2,
  Maximize2,
  MoreHorizontal,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageTime, RegenerateMessageAction } from "./MessageActions";
import { useCopyAction } from "./useCopyAction";

/** Signal-only summary (cost / rounds / duration) — token detail lives in「更多」. */
function MessageUsageSummary({
  rounds,
  costText,
  durationMs,
}: {
  rounds: number | undefined;
  costText: string | null;
  durationMs?: number;
}) {
  const durationText =
    durationMs != null && durationMs > 0 ? formatDuration(durationMs) : null;
  if ((rounds == null || rounds <= 1) && !costText && !durationText)
    return null;

  const parts: ReactNode[] = [];
  const pushSep = () => {
    if (parts.length > 0)
      parts.push(
        <span key={`sep-${parts.length}`} aria-hidden>
          ·
        </span>,
      );
  };

  if (costText) {
    pushSep();
    parts.push(<span key="cost">{costText}</span>);
  }
  if (rounds != null && rounds > 1) {
    pushSep();
    parts.push(<span key="rounds">{rounds} 轮</span>);
  }
  if (durationText) {
    pushSep();
    parts.push(<span key="dur">用时 {durationText}</span>);
  }

  return (
    <span className="inline-flex cursor-default items-center gap-1.5 text-xs tabular-nums text-muted-foreground/70">
      {parts}
    </span>
  );
}

function UsageDetailPanel({ usage }: { usage: UsageBreakdown }) {
  const cache = cacheUsageDisplay(usage);
  return (
    <div className="space-y-1 px-3 py-1.5 text-xs text-muted-foreground">
      <div className="flex justify-between gap-3 tabular-nums">
        <span>输入</span>
        <span className="text-foreground">{formatCompact(usage.input)}</span>
      </div>
      {cache.billedAsMiss ? (
        <div className="flex justify-between gap-3 tabular-nums">
          <span>{CACHE_BILLED_AS_MISS_LABEL}</span>
          <span className="text-foreground">
            {formatCompact(cache.cacheMiss)}
          </span>
        </div>
      ) : (
        <>
          <div className="flex justify-between gap-3 tabular-nums">
            <span>缓存命中</span>
            <span className="text-foreground">
              {formatCompact(cache.cacheHit)}
              {cache.hitRatePercent != null
                ? ` · ${cache.hitRatePercent}%`
                : ""}
            </span>
          </div>
          <div className="flex justify-between gap-3 tabular-nums">
            <span>缓存未命中</span>
            <span className="text-foreground">
              {formatCompact(cache.cacheMiss)}
            </span>
          </div>
        </>
      )}
      <div className="flex justify-between gap-3 tabular-nums">
        <span>输出</span>
        <span className="text-foreground">{formatCompact(usage.output)}</span>
      </div>
      {usage.reasoning > 0 && (
        <div className="flex justify-between gap-3 tabular-nums">
          <span>思考</span>
          <span className="text-foreground">
            {formatCompact(usage.reasoning)}
          </span>
        </div>
      )}
    </div>
  );
}

async function copyDiagnostic(
  label: string,
  value: string,
  description?: string,
) {
  if (await copyText(value)) notifySuccess(`已复制 ${label}`, { description });
}

/** 消息永久链接 (对话基础功能补齐): a hash anchor that reopens the conversation and
 * lands on this exact turn (scroll). Portable to the web build as a real
 * shareable URL; in desktop it round-trips through the same #/conversations/:id?msg=
 * route ConversationPage honors on load. */
function messagePermalink(conversationId: string, messageId: string): string {
  const base = window.location.href.split("#")[0];
  return `${base}#/conversations/${conversationId}?msg=${messageId}`;
}

function MessageMoreMenu({
  message,
  captainContext,
  finishReason,
}: {
  message: Message;
  captainContext: ContextBlockWire[];
  finishReason: string | undefined;
}) {
  const [contextOpen, setContextOpen] = useState(false);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const navigate = useNavigate();

  // 「复制排查包」恒可用（对齐错误卡；行业常见：支持 ID 可复制，底层检视才 gated）。
  // 诊断模式只管运行详情里的裸 ID / 调度埋点等噪声，不挡报障出口。
  const serverMessageId = assistantProjectionId(message);
  const diagnosticIds = {
    conversationId,
    messageId: serverMessageId,
    userMessageId: precedingUserMessageId(
      getActiveRuntime().messages,
      message.id,
    ),
    traceId: message.traceId,
    executionId: message.executionId,
    ...supportDiagnosticExtrasFromError(message.error),
  };
  const diagnosticText = formatSupportDiagnosticText(diagnosticIds);
  const finishLabel = finishReason
    ? FINISH_REASON_META[finishReason]?.label
    : null;

  const usage = message.usage;
  const hasSpendUsage = !!usage && (usage.input > 0 || usage.output > 0);
  const hasMenu =
    !!conversationId ||
    captainContext.length > 0 ||
    !!message.executionId ||
    hasSpendUsage ||
    !!diagnosticText ||
    !!finishLabel;

  const openInCanvas = () => {
    if (!conversationId || !message.executionId) return;
    navigate(turnDetailPath(conversationId, serverMessageId));
  };

  if (!hasMenu) return null;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton size="sm" aria-label="更多">
            <MoreHorizontal size={14} />
          </IconButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-48">
          {conversationId && (
            <DropdownMenuItem
              onSelect={() =>
                void copyDiagnostic(
                  "消息链接",
                  messagePermalink(conversationId, serverMessageId),
                )
              }
            >
              <Link2 size={14} className="shrink-0 text-muted-foreground" />
              复制消息链接
            </DropdownMenuItem>
          )}
          {captainContext.length > 0 && (
            <DropdownMenuItem onSelect={() => setContextOpen(true)}>
              <Layers size={14} className="shrink-0 text-muted-foreground" />
              收到的上下文 · {captainContext.length} 段
            </DropdownMenuItem>
          )}
          {message.executionId && conversationId && (
            <DropdownMenuItem onSelect={openInCanvas}>
              <Maximize2 size={14} className="shrink-0 text-muted-foreground" />
              在画布查看此回合
            </DropdownMenuItem>
          )}
          {hasSpendUsage && usage && (
            <>
              {(!!conversationId ||
                captainContext.length > 0 ||
                message.executionId) && <DropdownMenuSeparator />}
              <DropdownMenuLabel>用量详情</DropdownMenuLabel>
              <UsageDetailPanel usage={usage} />
              {message.rounds != null && message.rounds > 1 && (
                <div className="flex justify-between gap-3 px-3 pb-1.5 text-xs text-muted-foreground">
                  <span>ReAct 轮次</span>
                  <span className="tabular-nums text-foreground">
                    {message.rounds} 轮
                  </span>
                </div>
              )}
            </>
          )}
          {finishLabel && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>收尾原因</DropdownMenuLabel>
              <p className="px-3 pb-1.5 text-xs text-muted-foreground">
                {finishLabel}
              </p>
            </>
          )}
          {diagnosticText && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() => {
                  void buildSupportDiagnosticPack(diagnosticIds).then(
                    (text) => {
                      if (text) void copyDiagnostic("排查包", text);
                    },
                  );
                }}
              >
                <Fingerprint
                  size={14}
                  className="shrink-0 text-muted-foreground"
                />
                复制排查包
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <ReceivedContextDialog
        blocks={captainContext}
        open={contextOpen}
        onOpenChange={setContextOpen}
      />
    </>
  );
}

/** 回复反馈 (点赞/点踩, 对话基础功能补齐): thumbs up/down on an assistant reply. The active
 * side highlights in the brand color; clicking it again clears the rating (toggle off).
 * Optimistic — the service flips the bubble immediately and reverts on a failed persist. */
function FeedbackButtons({ message }: { message: Message }) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const feedback = message.feedback ?? null;
  const rate = (side: "up" | "down") => {
    if (!conversationId) return;
    const next = feedback === side ? null : side;
    void setMessageFeedback(conversationId, message.id, next).catch((err) =>
      notifyError(err, "反馈失败"),
    );
  };
  return (
    <>
      <SimpleTooltip label="有帮助">
        <IconButton
          size="sm"
          aria-label="有帮助"
          aria-pressed={feedback === "up"}
          className={feedback === "up" ? "text-primary" : undefined}
          onClick={() => rate("up")}
        >
          <ThumbsUp size={14} />
        </IconButton>
      </SimpleTooltip>
      <SimpleTooltip label="没帮助">
        <IconButton
          size="sm"
          aria-label="没帮助"
          aria-pressed={feedback === "down"}
          className={feedback === "down" ? "text-primary" : undefined}
          onClick={() => rate("down")}
        >
          <ThumbsDown size={14} />
        </IconButton>
      </SimpleTooltip>
    </>
  );
}

/** 消息收藏 (方向 4): star an assistant reply → 侧栏「已收藏」. Cross-device (server-
 * stored); optimistic via the bookmark store, filled when saved. */
function BookmarkButton({ message }: { message: Message }) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const bookmarked = useBookmarkStore((s) => s.ids.has(message.id));
  const toggle = useBookmarkStore((s) => s.toggle);
  return (
    <SimpleTooltip label={bookmarked ? "取消收藏" : "收藏"}>
      <IconButton
        size="sm"
        aria-label={bookmarked ? "取消收藏" : "收藏"}
        aria-pressed={bookmarked}
        className={bookmarked ? "text-primary" : undefined}
        onClick={() => {
          if (conversationId) void toggle(conversationId, message.id);
        }}
      >
        <Bookmark
          size={14}
          className={bookmarked ? "fill-current" : undefined}
        />
      </IconButton>
    </SimpleTooltip>
  );
}

/** Assistant bubble footer — actions left, usage summary + time right, low-freq in「更多」. */
export function AssistantMessageFooter({
  message,
  captainContext,
  costText,
  finishReason,
  onRegenerate,
  displayError,
}: {
  message: Message;
  captainContext: ContextBlockWire[];
  costText: string | null;
  finishReason: string | undefined;
  onRegenerate: () => void;
  /** Settled empty-failure card (message.error or synthetic); feeds copy via visibleMessageText. */
  displayError?: { code: string; message: string } | null;
}) {
  const hasProcess = (message.process?.length ?? 0) > 0;
  // Prefer displayError so synthesizable empty failures (no error payload) still copy.
  const exportError = {
    error: displayError ?? message.error,
    runs: message.runs,
  };
  const { copied, onCopy } = useCopyAction(() =>
    formatMessageExport(
      message.content,
      message.process,
      "deliverable",
      exportError,
    ),
  );
  const { copied: copiedProcess, onCopy: onCopyProcess } = useCopyAction(() =>
    formatMessageExport(
      message.content,
      message.process,
      "with_process",
      exportError,
    ),
  );
  return (
    <div className="mt-1 flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        {hasProcess ? (
          <DropdownMenu>
            <SimpleTooltip label={copied || copiedProcess ? "已复制" : "复制"}>
              <DropdownMenuTrigger asChild>
                <IconButton size="sm" aria-label="复制">
                  {copied || copiedProcess ? (
                    <Check size={14} />
                  ) : (
                    <Copy size={14} />
                  )}
                </IconButton>
              </DropdownMenuTrigger>
            </SimpleTooltip>
            <DropdownMenuContent align="start" className="min-w-40">
              <DropdownMenuItem onSelect={() => void onCopy()}>
                仅交付
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void onCopyProcess()}>
                含过程
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <SimpleTooltip label={copied ? "已复制" : "复制"}>
            <IconButton
              size="sm"
              aria-label="复制"
              onClick={() => void onCopy()}
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
            </IconButton>
          </SimpleTooltip>
        )}
        <FeedbackButtons message={message} />
        <BookmarkButton message={message} />
        <RegenerateMessageAction onRegenerate={onRegenerate} />
        <MessageMoreMenu
          message={message}
          captainContext={captainContext}
          finishReason={finishReason}
        />
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <MessageUsageSummary
          rounds={message.rounds}
          costText={costText}
          durationMs={message.durationMs}
        />
        <MessageTime iso={message.createdAt} />
      </div>
    </div>
  );
}
