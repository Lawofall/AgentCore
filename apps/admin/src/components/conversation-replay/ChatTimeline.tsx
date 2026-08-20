import { ChatView } from "@/components/chat/ChatView";
import { UserBubble } from "@/components/chat/UserBubble";
import { chatTurnFromReplay } from "@/components/chat/chatTurn";
import {
  EmptyPanel,
  ROLE_LABEL,
} from "@/components/conversation-replay/shared";
import { Spinner } from "@/components/ui/Spinner";
import { isExecutionHarvestMessage } from "@/lib/executionHarvest";
import { cn, fmtTime } from "@/lib/utils";
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
  hydratingId = null,
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
  hydratingId?: string | null;
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
        "flex min-w-0 flex-col gap-4 overflow-y-auto pr-0.5",
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
              hydrating={m.id === hydratingId}
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
        "flex min-w-0 w-full justify-end outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-xl",
        selected && "ring-1 ring-primary/30",
        anchored && !selected && "ring-1 ring-primary/20",
      )}
    >
      <div
        className={cn(
          "min-w-0 max-w-[min(100%,36rem)] rounded-xl rounded-br-none border px-4 py-2.5",
          selected
            ? "border-primary/40 bg-primary/10"
            : "border-border/60 bg-muted/50",
        )}
      >
        <div className="mb-1 flex items-center gap-2 text-muted-foreground text-xs">
          <span className="font-medium text-foreground">
            {ROLE_LABEL.user}
          </span>
          <span className="tabular-nums">{fmtTime(message.created_at)}</span>
        </div>
        <UserBubble
          content={message.content}
          attachments={message.attachments}
          agentMentions={message.agent_mentions}
        />
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
        "min-w-0 w-full max-w-[min(100%,48rem)] rounded-xl px-1 py-1 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
        selected && "ring-1 ring-primary/25",
        anchored && !selected && "ring-1 ring-primary/15",
      )}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">
          {ROLE_LABEL.assistant}
        </span>
        <span className="text-muted-foreground text-xs tabular-nums">
          {fmtTime(message.created_at)}
        </span>
      </div>
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
        content={turn.content}
        runsPayload={turn.runsPayload}
        projected={turn.projected}
        selectedRunId={selectedRunId}
        onSelectRun={onSelectRun}
      />
    </div>
  );
}
