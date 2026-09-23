import { ReceivedContextDialog } from "@/components/chat/ReceivedContext";
import { IconButton } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { copyText } from "@/lib/clipboard";
import { formatDuration, formatOutputSpeed, formatUsageCount } from "@/lib/format";
import { MESSAGE_ACTION_REVEAL_CLASS } from "@/lib/messageActionReveal";
import { formatMessageExport } from "@/lib/messageExport";
import { completedAtIso } from "@/lib/runningElapsed";
import {
  buildSupportDiagnosticPack,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportDiagnosticExtrasFromError,
} from "@/lib/supportDiagnostics";
import { cn } from "@/lib/utils";
import type { UsageBreakdown } from "@/services/usage";
import type { Message } from "@/stores/conversation";
import {
  assistantProjectionId,
  getActiveRuntime,
  useConversationStore,
} from "@/stores/conversation";
import type { ContextBlockWire } from "@/types/events";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheUsageDisplay,
} from "@agentcore/protocol-fold-kit";
import { Check, Copy, Gauge, Layers, Package } from "lucide-react";
import { type ReactNode, useState } from "react";
import {
  CloneMessageAction,
  MessageTime,
  RegenerateMessageAction,
} from "./MessageActions";
import { useCopyAction } from "./useCopyAction";

/** Signal-only summary (cost / duration) — token tree and ReAct rounds live in「用量」. */
export function AssistantMessageMetaSummary({
  costText,
  durationMs,
}: {
  costText: string | null;
  durationMs?: number;
}) {
  const durationText =
    durationMs != null && durationMs > 0 ? formatDuration(durationMs) : null;
  if (!costText && !durationText) return null;

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
  if (durationText) {
    pushSep();
    parts.push(
      <span key="dur" aria-label={`用时 ${durationText}`}>
        {durationText}
      </span>,
    );
  }

  return (
    <span className="inline-flex cursor-default items-center gap-1.5 text-xs tabular-nums text-muted-foreground/70">
      {parts}
    </span>
  );
}

function UsageRow({
  label,
  value,
  nested = false,
}: {
  label: string;
  value: string;
  nested?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex justify-between gap-3 tabular-nums",
        nested && "pl-3",
      )}
    >
      <span className="shrink-0">{label}</span>
      <span className="text-right text-foreground">{value}</span>
    </div>
  );
}

function UsageDetailPanel({
  usage,
  generationMs,
}: {
  usage: UsageBreakdown;
  generationMs?: number;
}) {
  const cache = cacheUsageDisplay(usage);
  const speedText =
    generationMs != null ? formatOutputSpeed(usage.output, generationMs) : null;
  const hitText =
    cache.hitRatePercent != null
      ? `${formatUsageCount(cache.cacheHit)} · ${cache.hitRatePercent}%`
      : formatUsageCount(cache.cacheHit);
  return (
    <div className="space-y-1 px-3 py-1.5 text-xs text-muted-foreground">
      <UsageRow label="输入" value={formatUsageCount(usage.input)} />
      {cache.billedAsMiss ? (
        <UsageRow
          label={CACHE_BILLED_AS_MISS_LABEL}
          value={formatUsageCount(cache.cacheMiss)}
          nested
        />
      ) : (
        <>
          {cache.cacheHit > 0 ? (
            <UsageRow label="缓存命中" value={hitText} nested />
          ) : null}
          {cache.cacheMiss > 0 ? (
            <UsageRow
              label="缓存未命中"
              value={formatUsageCount(cache.cacheMiss)}
              nested
            />
          ) : null}
        </>
      )}
      <UsageRow label="输出" value={formatUsageCount(usage.output)} />
      {usage.reasoning > 0 ? (
        <UsageRow
          label="思考"
          value={formatUsageCount(usage.reasoning)}
          nested
        />
      ) : null}
      {speedText ? <UsageRow label="速度" value={speedText} nested /> : null}
    </div>
  );
}

function ReceivedContextButton({
  blocks,
  process,
}: {
  blocks: ContextBlockWire[];
  process: Message["process"];
}) {
  const [open, setOpen] = useState(false);
  if (blocks.length === 0) return null;
  return (
    <>
      <SimpleTooltip label="收到的上下文">
        <IconButton
          size="sm"
          aria-label="收到的上下文"
          onClick={() => setOpen(true)}
        >
          <Layers size={14} />
        </IconButton>
      </SimpleTooltip>
      <ReceivedContextDialog
        blocks={blocks}
        process={process}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}

function UsagePopoverButton({ message }: { message: Message }) {
  const usage = message.usage;
  const hasSpendUsage = !!usage && (usage.input > 0 || usage.output > 0);
  if (!hasSpendUsage || !usage) return null;
  return (
    <Popover>
      <SimpleTooltip label="用量">
        <PopoverTrigger asChild>
          <IconButton size="sm" aria-label="用量">
            <Gauge size={14} />
          </IconButton>
        </PopoverTrigger>
      </SimpleTooltip>
      <PopoverContent align="start" className="w-52 p-0">
        <UsageDetailPanel usage={usage} generationMs={message.generationMs} />
        {message.rounds != null && message.rounds > 1 && (
          <div className="flex justify-between gap-3 px-3 pb-1.5 text-xs text-muted-foreground">
            <span>ReAct 轮次</span>
            <span className="tabular-nums text-foreground">
              {message.rounds} 轮
            </span>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

function CopySupportPackButton({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false);
  const conversationId = useConversationStore((s) => s.currentConversationId);
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
  if (!formatSupportDiagnosticText(diagnosticIds)) return null;

  const onCopy = async () => {
    const text = await buildSupportDiagnosticPack(diagnosticIds);
    if (text && (await copyText(text))) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <SimpleTooltip label={copied ? "已复制" : "复制排查包"}>
      <IconButton
        size="sm"
        aria-label="复制排查包"
        onClick={() => void onCopy()}
      >
        {copied ? <Check size={14} /> : <Package size={14} />}
      </IconButton>
    </SimpleTooltip>
  );
}

/** Per-turn inspect: snapshot, token detail, support pack. Each slot hides when empty. */
export function AssistantTurnInspect({
  message,
  captainContext,
  showContext = true,
  showUsage = true,
  showSupportPack = true,
}: {
  message: Message;
  captainContext: ContextBlockWire[];
  showContext?: boolean;
  showUsage?: boolean;
  /** False when this bubble is not allowed to host the pack. */
  showSupportPack?: boolean;
}) {
  return (
    <>
      {showContext ? (
        <ReceivedContextButton
          blocks={captainContext}
          process={message.process}
        />
      ) : null}
      {showUsage ? <UsagePopoverButton message={message} /> : null}
      {showSupportPack ? <CopySupportPackButton message={message} /> : null}
    </>
  );
}

/** Assistant bubble footer — actions left, ¥ + time right. Inspect sits with the actions. */
export function AssistantMessageFooter({
  message,
  captainContext,
  costText,
  onRegenerate,
  displayError,
  pinSupportPack = false,
  showSupportPack = true,
  showRegenerate,
}: {
  message: Message;
  captainContext: ContextBlockWire[];
  costText: string | null;
  onRegenerate: () => void;
  /** Settled failure face; feeds copy via visibleMessageText. */
  displayError?: { code: string; message: string } | null;
  /** Keep「复制排查包」visible (not hover-reveal) when this turn owes a pack. */
  pinSupportPack?: boolean;
  /** False when this bubble is not the pack host. */
  showSupportPack?: boolean;
  /** Arbitrator: hide when a named recovery is already the unique retry. */
  showRegenerate: boolean;
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
  const inspect = (
    <AssistantTurnInspect
      message={message}
      captainContext={captainContext}
      showSupportPack={showSupportPack && !pinSupportPack}
    />
  );
  const pinnedPack = pinSupportPack ? (
    <AssistantTurnInspect
      message={message}
      captainContext={captainContext}
      showContext={false}
      showUsage={false}
      showSupportPack={showSupportPack}
    />
  ) : null;
  return (
    <div className="mt-1 flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-0.5">
        <div
          className={cn(
            "flex min-w-0 items-center gap-0.5",
            MESSAGE_ACTION_REVEAL_CLASS,
          )}
        >
          {hasProcess ? (
            <DropdownMenu>
              <SimpleTooltip
                label={copied || copiedProcess ? "已复制" : "复制"}
              >
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
              <DropdownMenuContent align="start">
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
          {showRegenerate ? (
            <RegenerateMessageAction onRegenerate={onRegenerate} />
          ) : null}
          <CloneMessageAction messageId={message.id} />
          {inspect}
        </div>
        {pinnedPack}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <AssistantMessageMetaSummary
          costText={costText}
          durationMs={message.durationMs}
        />
        <MessageTime
          iso={completedAtIso(message.createdAt, message.durationMs)}
        />
      </div>
    </div>
  );
}
