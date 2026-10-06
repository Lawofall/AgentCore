import { ChatView } from "@/components/chat/ChatView";
import {
  ConversationHydrateOverlay,
  type ConversationHydratePhase,
} from "@/components/chat/ConversationHydrateOverlay";
import { SidePanelToggle } from "@/components/layout/SidePanelToggle";
import { IconButton } from "@/components/ui";
import { useConversations } from "@/hooks/useConversations";
import { ChatPaneProvider } from "@/lib/chatPane";
import { clampSplitRatio } from "@/lib/conversationSplit";
import {
  applySplitRatio,
  closeConversationPane,
  focusConversationPane,
} from "@/lib/conversationSplitActions";
import { cn } from "@/lib/utils";
import { loadLatestWindow } from "@/services/messages";
import { hasLocalConversationStream } from "@/services/turns/streamOwnership";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { useConversationSplitStore } from "@/stores/conversationSplit";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

function paneTitle(
  id: string | null,
  titles: ReadonlyMap<string, string>,
): string {
  if (!id) return "新对话";
  return titles.get(id)?.trim() || "对话";
}

function useBesideHydrate(
  id: string | null,
  enabled: boolean,
): { phase: ConversationHydratePhase; retry: () => void } {
  const [phase, setPhase] = useState<ConversationHydratePhase>(
    enabled && id ? "loading" : "ready",
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled || !id) {
      setPhase("ready");
      return;
    }
    const rt = getRuntime(id);
    if (rt.messages.length > 0 || hasLocalConversationStream(id)) {
      setPhase("ready");
      return;
    }
    // attempt 只当「再试一次」的计数，变了就重跑。
    void attempt;
    let cancelled = false;
    setPhase("loading");
    useConversationStore.getState().ensureResidentSlice(id);
    void loadLatestWindow(id)
      .then((ok) => {
        if (cancelled) return;
        setPhase(ok || getRuntime(id).messages.length > 0 ? "ready" : "error");
      })
      .catch(() => {
        if (!cancelled) setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [id, enabled, attempt]);

  return {
    phase: enabled ? phase : "ready",
    retry: () => setAttempt((n) => n + 1),
  };
}

function SplitPane({
  id,
  index,
  focused,
  title,
  hydratePhase,
  onHydrateRetry,
  showDockToggle,
}: {
  id: string | null;
  index: 0 | 1;
  focused: boolean;
  title: string;
  hydratePhase: ConversationHydratePhase;
  onHydrateRetry?: () => void;
  showDockToggle: boolean;
}) {
  const navigate = useNavigate();
  return (
    <ChatPaneProvider id={id}>
      <section
        className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
        data-conversation-pane={index}
        data-focused={focused ? "true" : "false"}
        aria-label={title}
        onPointerDown={() => {
          if (!focused) focusConversationPane(index, navigate);
        }}
      >
        <div
          data-pane-header=""
          className={cn(
            "flex h-10 shrink-0 items-center gap-2 border-b px-3",
            focused
              ? "border-border bg-accent/40 text-foreground"
              : "border-border text-muted-foreground",
          )}
        >
          <span className="min-w-0 flex-1 truncate text-sm">{title}</span>
          <div
            className="flex shrink-0 items-center gap-1"
            onPointerDown={(e) => e.stopPropagation()}
          >
            {showDockToggle && <SidePanelToggle />}
            <IconButton
              size="md"
              aria-label="关闭这一栏"
              className="border border-border bg-card"
              onClick={(e) => {
                e.stopPropagation();
                closeConversationPane(index, navigate);
              }}
            >
              <X size={16} />
            </IconButton>
          </div>
        </div>
        <div
          data-pane-body=""
          className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
        >
          <ChatView layout="split" ownsFind={focused} />
          {id && (
            <ConversationHydrateOverlay
              phase={hydratePhase}
              onRetry={onHydrateRetry}
            />
          )}
        </div>
      </section>
    </ChatPaneProvider>
  );
}

export function ConversationSplitView({
  routeHydratePhase,
  onRouteHydrateRetry,
  showDockToggle,
}: {
  routeHydratePhase: ConversationHydratePhase;
  onRouteHydrateRetry: () => void;
  showDockToggle: boolean;
}) {
  const split = useConversationSplitStore((s) => s.split);
  const conversations = useConversations();
  const titles = new Map(conversations.map((c) => [c.id, c.title]));
  const beside = useBesideHydrate(
    split ? split.panes[1 - split.focus] : null,
    Boolean(split),
  );
  if (!split) return null;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
      <div
        className="flex min-h-0 min-w-0 flex-col overflow-hidden"
        style={{ flex: `${split.ratio} 1 0%` }}
      >
        <SplitPane
          id={split.panes[0]}
          index={0}
          focused={split.focus === 0}
          title={paneTitle(split.panes[0], titles)}
          hydratePhase={split.focus === 0 ? routeHydratePhase : beside.phase}
          onHydrateRetry={
            split.focus === 0 ? onRouteHydrateRetry : beside.retry
          }
          showDockToggle={false}
        />
      </div>
      <SplitSash />
      <div
        className="flex min-h-0 min-w-0 flex-col overflow-hidden"
        style={{ flex: `${1 - split.ratio} 1 0%` }}
      >
        <SplitPane
          id={split.panes[1]}
          index={1}
          focused={split.focus === 1}
          title={paneTitle(split.panes[1], titles)}
          hydratePhase={split.focus === 1 ? routeHydratePhase : beside.phase}
          onHydrateRetry={
            split.focus === 1 ? onRouteHydrateRetry : beside.retry
          }
          showDockToggle={showDockToggle}
        />
      </div>
    </div>
  );
}

function SplitSash() {
  const ratio = useConversationSplitStore((s) => s.split?.ratio ?? 0.5);
  return (
    <div
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label="调整两栏宽度"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(ratio * 100)}
      className="w-1.5 shrink-0 cursor-col-resize bg-border/80 hover:bg-primary/40 focus-visible:bg-primary/40 focus-visible:outline-none"
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const frame = event.currentTarget.parentElement;
        if (!frame) return;
        const width = frame.clientWidth;
        const delta = event.key === "ArrowRight" ? 16 : -16;
        applySplitRatio(clampSplitRatio(ratio + delta / width, width));
      }}
      onPointerDown={(event) => {
        event.preventDefault();
        event.stopPropagation();
        const frame = event.currentTarget.parentElement;
        if (!frame) return;
        const width = frame.clientWidth;
        const startX = event.clientX;
        const start = ratio;
        const move = (ev: PointerEvent) => {
          applySplitRatio(
            clampSplitRatio(start + (ev.clientX - startX) / width, width),
          );
        };
        const up = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      }}
    />
  );
}
