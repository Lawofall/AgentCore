import { ChatView } from "@/components/chat/ChatView";
import { UserBubble } from "@/components/chat/UserBubble";
import { chatTurnFromReplay } from "@/components/chat/chatTurn";
import { EmptyPanel } from "@/components/conversation-replay/shared";
import { Spinner } from "@/components/ui/Spinner";
import { isExecutionHarvestMessage } from "@/lib/executionHarvest";
import { cn, fmtCny, fmtMs, fmtTime, nanoToYuan } from "@/lib/utils";
import type { ReplayMessage } from "@/services/adminObservability";
import { type KeyboardEvent, useEffect, useMemo, useRef } from "react";

/**
 * User-perspective replay column. Assistant rows go through ChatView
 * (`chatTurnFromReplay`); harvest rows stay out of this lane.
 *
 * Bubble containers are click-to-select. Enter/Space on a nested control must
 * not be swallowed — selecting the turn is only what *this* element was asked
 * to do.
 */
function activateOnSelfKey(
  e: KeyboardEvent<HTMLDivElement>,
  onSelect: () => void,
): void {
  if (e.key !== "Enter" && e.key !== " ") return;
  if (e.target !== e.currentTarget) return;
  e.preventDefault();
  onSelect();
}

export function ChatTimeline({
  messages,
  selectedId,
  selectedRunId,
  onSelect,
  onSelectRun,
  isAnchored,
  hasMoreBefore = false,
  hydratingIds = [],
  hydrateError = null,
  onRetryHydrate,
  className,
}: {
  messages: ReplayMessage[];
  selectedId: string | null;
  selectedRunId: string | null;
  onSelect: (id: string) => void;
  onSelectRun: (runId: string) => void;
  isAnchored: (m: ReplayMessage) => boolean;
  hasMoreBefore?: boolean;
  hydratingIds?: readonly string[];
  hydrateError?: string | null;
  onRetryHydrate?: () => void;
  /** Sizing comes from the page's layout row — this pane just scrolls inside it. */
  className?: string;
}) {
  const refs = useRef<Map<string, HTMLDivElement>>(new Map());
  const visible = useMemo(
    () => messages.filter((m) => !isExecutionHarvestMessage(m)),
    [messages],
  );

  useEffect(() => {
    if (!selectedId) return;
    const el = refs.current.get(selectedId);
    el?.scrollIntoView?.({
      behavior: "smooth",
      block: "nearest",
    });
  }, [selectedId]);

  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-5",
        className,
      )}
    >
      {hasMoreBefore && (
        <div
          role="status"
          className="rounded-lg border border-dashed border-border bg-muted/40 px-3 py-2 text-muted-foreground text-xs"
        >
          更早的消息已被截断，当前只显示最近窗口
        </div>
      )}
      {visible.map((m) => (
        <div
          key={m.id}
          className="w-full min-w-0 shrink-0"
          ref={(node) => {
            if (node) refs.current.set(m.id, node);
            else refs.current.delete(m.id);
          }}
        >
          {m.role === "user" ? (
            <UserTurn
              message={m}
              selected={m.id === selectedId}
              anchored={isAnchored(m)}
              onSelect={() => onSelect(m.id)}
            />
          ) : (
            <AssistantTurn
              message={m}
              selected={m.id === selectedId}
              selectedRunId={m.id === selectedId ? selectedRunId : null}
              anchored={isAnchored(m)}
              hydrating={
                Boolean(m.has_final_state) &&
                hydratingIds.includes(m.id) &&
                m.runs_payload == null &&
                m.projected == null
              }
              hydrateError={m.id === selectedId ? hydrateError : null}
              onRetryHydrate={onRetryHydrate}
              onSelect={() => onSelect(m.id)}
              onSelectRun={(runId) => {
                onSelect(m.id);
                onSelectRun(runId);
              }}
            />
          )}
        </div>
      ))}
      {visible.length === 0 && <EmptyPanel text="该会话暂无用户可见消息" />}
    </div>
  );
}

function UserTurn({
  message,
  selected,
  anchored,
  onSelect,
}: {
  message: ReplayMessage;
  selected: boolean;
  anchored: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      onKeyDown={(e) => activateOnSelfKey(e, onSelect)}
      className={cn(
        "group flex min-w-0 w-full justify-end rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected && "ring-1 ring-primary/25",
        anchored && !selected && "ring-1 ring-primary/15",
      )}
    >
      <div className="flex min-w-0 max-w-[80%] flex-col items-end gap-1">
        <div className="min-w-0 w-full rounded-xl rounded-br-none bg-muted px-4 py-3 text-sm text-foreground">
          <UserBubble
            content={message.content}
            attachments={message.attachments}
            agentMentions={message.agent_mentions}
          />
        </div>
        <span className="text-muted-foreground text-xs tabular-nums opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          {fmtTime(message.created_at)}
        </span>
      </div>
    </div>
  );
}

function AssistantTurn({
  message,
  selected,
  selectedRunId,
  anchored,
  hydrating,
  hydrateError,
  onRetryHydrate,
  onSelect,
  onSelectRun,
}: {
  message: ReplayMessage;
  selected: boolean;
  selectedRunId: string | null;
  anchored: boolean;
  hydrating: boolean;
  hydrateError: string | null;
  onRetryHydrate?: () => void;
  onSelect: () => void;
  onSelectRun: (runId: string) => void;
}) {
  const turn = chatTurnFromReplay(message);

  return (
    <div
      role="button"
      tabIndex={0}
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      onKeyDown={(e) => activateOnSelfKey(e, onSelect)}
      className={cn(
        "group min-w-0 w-full rounded-xl px-1 py-1 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
        selected && "bg-muted/40",
        anchored && !selected && "bg-muted/20",
      )}
    >
      <span className="mb-1 block text-muted-foreground text-xs tabular-nums opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        {fmtTime(message.created_at)}
      </span>
      {hydrating && (
        <p
          role="status"
          className="mb-2 flex items-center gap-1.5 text-muted-foreground text-xs"
        >
          <Spinner className="size-3" />
          正在加载终态
        </p>
      )}
      {hydrateError && !hydrating && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          <p className="text-destructive">{hydrateError}</p>
          {onRetryHydrate && (
            <button
              type="button"
              className="rounded-md border border-border px-2 py-0.5 text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
              onClick={(e) => {
                e.stopPropagation();
                onRetryHydrate();
              }}
            >
              重试加载终态
            </button>
          )}
        </div>
      )}
      <ChatView
        {...turn}
        selectedRunId={selectedRunId}
        onSelectRun={onSelectRun}
      />
      <AssistantUsageFooter message={message} />
    </div>
  );
}

/** Desktop AssistantMessageFooter analog — cost · rounds · duration only. */
function AssistantUsageFooter({ message }: { message: ReplayMessage }) {
  const cost =
    message.cost_total > 0 ? fmtCny(nanoToYuan(message.cost_total)) : null;
  const rounds = message.metrics?.rounds;
  const durationMs = message.metrics?.duration_ms;
  const parts: string[] = [];
  if (cost) parts.push(cost);
  if (rounds != null && rounds > 1) parts.push(`${rounds} 轮`);
  if (durationMs != null && durationMs > 0) parts.push(fmtMs(durationMs));
  if (parts.length === 0) return null;
  return (
    <div
      aria-label="回合用量"
      className="mt-2 text-muted-foreground/70 text-xs tabular-nums"
    >
      {parts.join(" · ")}
    </div>
  );
}
