import { IconButton } from "@/components/ui";
import { textLinkPrimary } from "@/components/ui/tone-presets";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import {
  type DeveloperTrace,
  type JournalLine,
  type RunLlmWindowResponse,
  fetchCallWindow,
  fetchDeveloperTrace,
} from "@/services/developerTrace";
import {
  assistantProjectionId,
  runtimeOf,
  useConversationStore,
} from "@/stores/conversation";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Check, ChevronRight, Copy } from "lucide-react";
import { type RefObject, useEffect, useRef, useState } from "react";

const VIRTUAL_AT = 40;

/**
 * Developer-mode log: one window fact per line, payload as stored.
 * Display events that only paint the chat are omitted by the trace API.
 * An llm_call can also dump the window folded for that call, as JSON.
 */
export function TurnTracePanel({
  conversationId,
  messageId,
}: {
  conversationId: string;
  messageId: string;
}) {
  const live = useConversationStore((s) => {
    const message = runtimeOf(s, conversationId).messages.find(
      (item) =>
        item.id === messageId || assistantProjectionId(item) === messageId,
    );
    return message?.status === "running" || message?.isStreaming === true;
  });
  const [view, setView] = useState<DeveloperTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [windowFor, setWindowFor] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const hasView = useRef(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: conversation and message are the reset key
  useEffect(() => {
    hasView.current = false;
    setView(null);
    setError(null);
    setExpanded(new Set());
    setWindowFor(null);
  }, [conversationId, messageId]);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchDeveloperTrace(conversationId, messageId)
        .then((next) => {
          if (cancelled) return;
          hasView.current = true;
          setView(next);
          setError(null);
        })
        .catch(() => {
          if (cancelled || hasView.current) return;
          setError("看不到这一回合的记录");
        });
    };
    load();
    if (!live) {
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [conversationId, live, messageId]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: view, expanded, and windowFor are re-run keys for stick-to-bottom
  useEffect(() => {
    if (!stickRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [view, expanded, windowFor]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const lines = view?.lines ?? [];

  return (
    <div className="absolute inset-0 flex flex-col" aria-label="这一回合">
      {view ? <TurnHeader view={view} /> : null}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {error ? (
          <p className="px-3 py-3 text-sm text-muted-foreground">{error}</p>
        ) : view == null ? (
          <p className="px-3 py-3 text-sm text-muted-foreground">正在读取…</p>
        ) : view.gap ? (
          <p className="px-3 py-3 text-sm text-muted-foreground">{view.gap}</p>
        ) : (
          <JournalList
            lines={lines}
            expanded={expanded}
            windowFor={windowFor}
            onToggle={toggle}
            onWindow={(id) =>
              setWindowFor((current) => (current === id ? null : id))
            }
            conversationId={conversationId}
            messageId={messageId}
            scrollRef={scrollRef}
          />
        )}
      </div>
    </div>
  );
}

function headerTitle(view: DeveloperTrace): string {
  const bits = [
    view.where,
    view.model,
    formatDuration(view.duration_ms),
  ].filter((bit): bit is string => Boolean(bit));
  if (view.in_progress) bits.push("至今");
  return bits.length > 0 ? bits.join(" · ") : "这一回合";
}

function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || ms <= 0) return null;
  if (ms < 1000) return `${ms} 毫秒`;
  const seconds = ms / 1000;
  if (seconds < 60) {
    const text = seconds.toFixed(1).replace(/0$/, "").replace(/\.$/, "");
    return `${text} 秒`;
  }
  let minutes = Math.floor(seconds / 60);
  let rem = Math.round(seconds - minutes * 60);
  if (rem === 60) {
    minutes += 1;
    rem = 0;
  }
  if (rem === 0) return `${minutes} 分`;
  return `${minutes} 分 ${rem} 秒`;
}

function lineLabel(line: JournalLine): string {
  const bits = [line.ts, line.kind, line.mark].filter((bit): bit is string =>
    Boolean(bit),
  );
  return bits.join(" ");
}

function canOpenWindow(line: JournalLine): boolean {
  return (
    line.kind === "llm_call" &&
    Boolean(line.run_id) &&
    typeof line.round_idx === "number"
  );
}

function TurnHeader({ view }: { view: DeveloperTrace }) {
  return (
    <header className="border-b border-border bg-card px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-foreground">{headerTitle(view)}</p>
          {view.trace_id ? (
            <p
              className="mt-2 truncate font-mono text-xs text-muted-foreground"
              title={view.trace_id}
            >
              {view.trace_id}
            </p>
          ) : null}
        </div>
        {view.trace_id ? <CopyIdButton text={view.trace_id} /> : null}
      </div>
    </header>
  );
}

function JournalList({
  lines,
  expanded,
  windowFor,
  onToggle,
  onWindow,
  conversationId,
  messageId,
  scrollRef,
}: {
  lines: JournalLine[];
  expanded: ReadonlySet<string>;
  windowFor: string | null;
  onToggle: (id: string) => void;
  onWindow: (id: string) => void;
  conversationId: string;
  messageId: string;
  scrollRef: RefObject<HTMLDivElement | null>;
}) {
  const virtual = lines.length >= VIRTUAL_AT;
  const virtualizer = useVirtualizer({
    count: virtual ? lines.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 32,
    overscan: 8,
  });

  if (!virtual) {
    return (
      <div>
        {lines.map((line) => (
          <JournalRow
            key={line.id}
            line={line}
            open={expanded.has(line.id)}
            windowOpen={windowFor === line.id}
            onToggle={() => onToggle(line.id)}
            onWindow={() => onWindow(line.id)}
            conversationId={conversationId}
            messageId={messageId}
          />
        ))}
      </div>
    );
  }

  return (
    <div
      className="relative w-full"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map((item) => {
        const line = lines[item.index];
        if (!line) return null;
        return (
          <div
            key={line.id}
            data-index={item.index}
            ref={virtualizer.measureElement}
            className="absolute left-0 top-0 w-full"
            style={{ transform: `translateY(${item.start}px)` }}
          >
            <JournalRow
              line={line}
              open={expanded.has(line.id)}
              windowOpen={windowFor === line.id}
              onToggle={() => onToggle(line.id)}
              onWindow={() => onWindow(line.id)}
              conversationId={conversationId}
              messageId={messageId}
            />
          </div>
        );
      })}
    </div>
  );
}

function JournalRow({
  line,
  open,
  windowOpen,
  onToggle,
  onWindow,
  conversationId,
  messageId,
}: {
  line: JournalLine;
  open: boolean;
  windowOpen: boolean;
  onToggle: () => void;
  onWindow: () => void;
  conversationId: string;
  messageId: string;
}) {
  const label = lineLabel(line);
  const windowable = canOpenWindow(line);
  return (
    <div className="border-b border-border">
      <div className="flex items-start gap-2 px-3 py-1.5">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-start gap-1 text-left"
          aria-expanded={open}
          onClick={onToggle}
        >
          <ChevronRight
            size={14}
            aria-hidden
            className={cn(
              "mt-0.5 shrink-0 text-muted-foreground",
              open && "rotate-90",
            )}
          />
          <span className="min-w-0 flex-1 break-all font-mono text-xs text-foreground">
            {label}
          </span>
        </button>
        {windowable ? (
          <button
            type="button"
            className={cn(textLinkPrimary, "shrink-0")}
            aria-expanded={windowOpen}
            onClick={onWindow}
          >
            窗口
          </button>
        ) : null}
      </div>
      {open ? <PayloadBlock payload={line.payload} /> : null}
      {windowOpen && windowable ? (
        <WindowDump
          conversationId={conversationId}
          messageId={messageId}
          runId={line.run_id as string}
          roundIdx={line.round_idx as number}
        />
      ) : null}
    </div>
  );
}

function PayloadBlock({ payload }: { payload: Record<string, unknown> }) {
  return (
    <pre className="mx-3 mb-2 overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-muted/30 px-2.5 py-2 font-mono text-xs text-foreground">
      {JSON.stringify(payload ?? {}, null, 2)}
    </pre>
  );
}

function WindowDump({
  conversationId,
  messageId,
  runId,
  roundIdx,
}: {
  conversationId: string;
  messageId: string;
  runId: string;
  roundIdx: number;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setError(false);
    fetchCallWindow(conversationId, messageId, runId, roundIdx)
      .then((next) => {
        if (cancelled) return;
        if (!next.available) {
          setError(true);
          return;
        }
        setText(windowJson(next));
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, messageId, roundIdx, runId]);

  if (error) {
    return (
      <p className="px-3 pb-2 text-xs text-muted-foreground">
        这次调用的窗口折不出来
      </p>
    );
  }
  if (text == null) {
    return <p className="px-3 pb-2 text-xs text-muted-foreground">正在读取…</p>;
  }
  return (
    <pre className="mx-3 mb-2 overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-muted/30 px-2.5 py-2 font-mono text-xs text-foreground">
      {text}
    </pre>
  );
}

function windowJson(view: RunLlmWindowResponse): string {
  return JSON.stringify(
    {
      new_from: view.new_from,
      compacted: view.compacted,
      history_omitted: view.history_omitted,
      messages: view.messages,
    },
    null,
    2,
  );
}

function CopyIdButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      size="sm"
      aria-label="复制编号"
      onClick={() => {
        void copyText(text).then((ok) => {
          if (!ok) return;
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </IconButton>
  );
}
