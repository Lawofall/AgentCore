import { DraftEmptyState } from "@/components/onboarding/DraftEmptyState";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useComposerDockFlip } from "@/hooks/useComposerDockFlip";
import { useConversations } from "@/hooks/useConversations";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { shouldCenterDraftComposer } from "@/lib/onboarding";
import { useChatScroll } from "@/lib/useChatScroll";
import { cn } from "@/lib/utils";
import {
  loadLatestWindow,
  loadNewerMessages,
  loadOlderMessages,
} from "@/services/messages";
import { useComposerDraftStore } from "@/stores/composer";
import {
  useActiveFirstMessageId,
  useActiveHasMessages,
  useActiveHasMoreAfter,
  useActiveHasMoreBefore,
  useActiveLoadingNewer,
  useActiveLoadingOlder,
  useConversationStore,
} from "@/stores/conversation";
import { ArrowDown, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ConversationDecisionPrompts } from "./ConversationDecisionPrompts";
import { ConversationOutline } from "./ConversationOutline";
import { FindBar } from "./FindBar";
import { MessageInput } from "./MessageInput";
import { MessageList } from "./MessageList";
import { StageCardDock } from "./StageCardDock";

export function ChatView() {
  const { isNarrow } = useNarrowLayoutState();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const hasMessages = useActiveHasMessages();
  const conversations = useConversations();
  // 草稿态（未落库对话）才可能进居中欢迎态。已落库对话切换时会先经历一个「历史尚未
  // 异步加载完」的空窗口，用 isDraft 把它挡在居中判定外，避免输入框「弹到中间再飞回底栏」。
  const isDraft = conversationId === null;
  // 例外：已落库但确定 0 消息的会话（演示磁带 prepare 绑定的空会话）也应像草稿一样居中欢迎——
  // 元数据 messageCount===0 = 确定为空（有消息的会话>0；历史加载中未入列表的会话查不到），
  // 故不会重蹈切会话抖动。
  const knownEmptyPersisted =
    !isDraft &&
    conversations.find((c) => c.id === conversationId)?.messageCount === 0;
  const centerComposer = shouldCenterDraftComposer({
    isDraft,
    hasMessages,
    knownEmptyPersisted,
  });
  const composerFlipRef = useRef<HTMLDivElement>(null);
  // 落地动画只由首发信号触发（草稿 promote 成新对话），而非被动的居中→底栏翻转——
  // 后者在切换对话时也会发生，正是「输入框一直在跳动」的来源。
  const dockFlipToken = useComposerDraftStore((s) => s.dockFlipToken);
  useComposerDockFlip(composerFlipRef, centerComposer, dockFlipToken);

  return (
    <div className="relative flex min-w-0 flex-1 flex-col">
      <ChatTranscriptPane isNarrow={isNarrow} />

      {/* Composer dock: empty draft → input at viewport center, greeting/chips
          above it; in-session → bottom bar. First send FLIPs input center→bottom. */}
      <div
        className={
          centerComposer
            ? "absolute inset-0 z-10 flex items-center justify-center overflow-y-auto py-10"
            : "mx-auto min-w-0 w-full max-w-3xl"
        }
        data-composer-dock={centerComposer ? "center" : "bottom"}
      >
        <div
          className={
            centerComposer
              ? "relative mx-auto min-w-0 w-full max-w-3xl"
              : undefined
          }
        >
          {centerComposer && (
            <div className="absolute inset-x-0 bottom-full mb-6">
              <DraftEmptyState />
            </div>
          )}
          {hasMessages && (
            <>
              <ConversationDecisionPrompts />
              <StageCardDock />
            </>
          )}
          <div ref={composerFlipRef}>
            <MessageInput
              className={centerComposer ? "px-4 pb-2" : undefined}
              variant={centerComposer ? "card" : "bar"}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/** Owns stick-to-bottom + find/outline so streaming ticks do not re-render the composer. */
function ChatTranscriptPane({ isNarrow }: { isNarrow: boolean }) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const hasMessages = useActiveHasMessages();
  const firstMessageId = useActiveFirstMessageId();
  const hasMoreBefore = useActiveHasMoreBefore();
  const hasMoreAfter = useActiveHasMoreAfter();
  const loadingOlder = useActiveLoadingOlder();
  const loadingNewer = useActiveLoadingNewer();

  const onLoadOlder = useCallback(() => {
    if (conversationId) void loadOlderMessages(conversationId);
  }, [conversationId]);
  const onLoadNewer = useCallback(() => {
    if (conversationId) void loadNewerMessages(conversationId);
  }, [conversationId]);
  const onJumpToLatest = useCallback(() => {
    if (conversationId) void loadLatestWindow(conversationId);
  }, [conversationId]);

  const [findOpen, setFindOpen] = useState(false);
  useEffect(() => {
    if (isNarrow) return;
    const onKey = (e: KeyboardEvent) => {
      if (
        (e.ctrlKey || e.metaKey) &&
        !e.altKey &&
        e.key.toLowerCase() === "f"
      ) {
        if (!hasMessages) return;
        e.preventDefault();
        setFindOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasMessages, isNarrow]);
  useEffect(() => {
    if (!hasMessages && findOpen) setFindOpen(false);
  }, [hasMessages, findOpen]);

  const { scrollRef, contentRef, atBottom, jumpToBottom } = useChatScroll({
    firstMessageId,
    hasTranscript: hasMessages,
    resetKey: conversationId,
    hasMoreBefore,
    hasMoreAfter,
    loadingOlder,
    loadingNewer,
    onLoadOlder,
    onLoadNewer,
    onJumpToLatest,
  });

  return (
    <div className="relative min-h-0 flex-1">
      {!isNarrow && (
        <FindBar open={findOpen} onClose={() => setFindOpen(false)} />
      )}
      {!isNarrow && <ConversationOutline />}
      <div ref={scrollRef} className="h-full overflow-y-auto">
        {hasMessages && (
          <div
            ref={contentRef}
            className={cn(
              "mx-auto min-w-0 w-full max-w-3xl space-y-4",
              isNarrow ? "px-4 pb-4 pt-4" : "px-6 pb-4 pt-10",
            )}
          >
            {(loadingOlder || hasMoreBefore) && (
              <div className="flex justify-center py-2">
                <Loader2
                  size={16}
                  className={`text-muted-foreground ${
                    loadingOlder ? "animate-spin" : "opacity-0"
                  }`}
                />
              </div>
            )}
            <MessageList />
            {loadingNewer && (
              <div className="flex justify-center py-2">
                <Loader2
                  size={16}
                  className="animate-spin text-muted-foreground"
                />
              </div>
            )}
          </div>
        )}
      </div>
      {hasMessages && !atBottom && (
        <SimpleTooltip label="回到底部">
          <IconButton
            size="md"
            onClick={jumpToBottom}
            aria-label="回到底部"
            className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-border bg-card text-muted-foreground shadow-md hover:text-foreground"
          >
            <ArrowDown size={16} />
          </IconButton>
        </SimpleTooltip>
      )}
    </div>
  );
}
