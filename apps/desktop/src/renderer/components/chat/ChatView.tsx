import { DraftEmptyState } from "@/components/onboarding/DraftEmptyState";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useComposerDockFlip } from "@/hooks/useComposerDockFlip";
import { useConversations } from "@/hooks/useConversations";
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
  useActiveHasMoreAfter,
  useActiveHasMoreBefore,
  useActiveLoadingNewer,
  useActiveLoadingOlder,
  useActiveMessages,
  useConversationStore,
} from "@/stores/conversation";
import { isToolGranted, usePendingApprovals } from "@/stores/interactions";
import { ArrowDown, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalPrompt } from "./ApprovalPrompt";
import { ConversationDecisionPrompts } from "./ConversationDecisionPrompts";
import { ConversationOutline } from "./ConversationOutline";
import { FindBar } from "./FindBar";
import { MessageInput } from "./MessageInput";
import { MessageList } from "./MessageList";
import { StageCardDock } from "./StageCardDock";

export function ChatView() {
  const messages = useActiveMessages();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const hasMessages = messages.length > 0;
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
  const pendingApprovals = usePendingApprovals(conversationId);
  // 工具审批 A：底栏态把确认条贴在输入框上方成一体；居中草稿无审批。
  const fuseApprovalComposer = useMemo(() => {
    if (!conversationId || centerComposer || !hasMessages) return false;
    return pendingApprovals.some(
      (p) => !isToolGranted(conversationId, p.toolName),
    );
  }, [centerComposer, conversationId, hasMessages, pendingApprovals]);
  const composerFlipRef = useRef<HTMLDivElement>(null);
  // 落地动画只由首发信号触发（草稿 promote 成新对话），而非被动的居中→底栏翻转——
  // 后者在切换对话时也会发生，正是「输入框一直在跳动」的来源。
  const dockFlipToken = useComposerDraftStore((s) => s.dockFlipToken);
  useComposerDockFlip(composerFlipRef, centerComposer, dockFlipToken);
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

  // 会话内查找: Ctrl/Cmd+F opens the find bar (`f` is free in GLOBAL_SHORTCUTS). Scoped to
  // when a non-empty conversation is on screen — ChatView only mounts then anyway. Esc /
  // the ✕ close it (handled inside FindBar).
  const [findOpen, setFindOpen] = useState(false);
  useEffect(() => {
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
  }, [hasMessages]);
  useEffect(() => {
    if (!hasMessages && findOpen) setFindOpen(false);
  }, [hasMessages, findOpen]);

  // Re-run the auto-follow whenever the newest turn grows — both its answer and
  // its live reasoning stream — so the view tracks the model while it thinks.
  const last = messages[messages.length - 1];
  const contentKey = last
    ? `${last.id}-${last.content.length}-${last.reasoning?.length ?? 0}`
    : "";

  const { scrollRef, contentRef, atBottom, jumpToBottom } = useChatScroll({
    messages,
    resetKey: conversationId,
    contentKey,
    hasMoreBefore,
    hasMoreAfter,
    loadingOlder,
    loadingNewer,
    onLoadOlder,
    onLoadNewer,
    onJumpToLatest,
  });

  return (
    <div className="relative flex min-w-0 flex-1 flex-col">
      {/* Scrollable message area (scrollbar at container edge, content centered).
          The relative wrapper anchors the floating 回到底部 button to the viewport
          so it stays put instead of scrolling away with the messages. */}
      <div className="relative min-h-0 flex-1">
        <FindBar open={findOpen} onClose={() => setFindOpen(false)} />
        <ConversationOutline />
        <div ref={scrollRef} className="h-full overflow-y-auto">
          {hasMessages && (
            <div
              ref={contentRef}
              className="mx-auto min-w-0 w-full max-w-3xl space-y-4 px-6 pb-4 pt-10"
            >
              {/* Headerless chat view: the top padding keeps the first message
                  clear of the floating side-panel toggle (top-right of the pane,
                  set in ConversationPage). */}
              {/* Top sentinel: spins while the previous page loads (scroll-up
                  infinite scroll); the window anchors so the view stays put. */}
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
              {/* Bottom sentinel: spins while a newer page loads (only reachable
                  after a search-hit jump left newer history unloaded). */}
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
          {/* 空态（草稿 / 已知空会话）不在滚动区渲染引导——它随居中输入框在下方
              composer dock 一起呈现（见 centerComposer）。已落库对话加载历史时既非
              草稿也未居中，此处留空，只余底栏输入框。 */}
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
              <ConversationDecisionPrompts
                omitApproval={fuseApprovalComposer}
              />
              <StageCardDock />
            </>
          )}
          <div
            ref={composerFlipRef}
            className={cn(fuseApprovalComposer && "px-4 pb-4")}
            data-approval-composer-fuse={
              fuseApprovalComposer ? "true" : undefined
            }
          >
            {fuseApprovalComposer && <ApprovalPrompt attached />}
            <MessageInput
              className={
                fuseApprovalComposer
                  ? "px-0 pb-0 pt-0"
                  : centerComposer
                    ? "px-4 pb-2"
                    : undefined
              }
              variant={centerComposer ? "card" : "bar"}
              attachedBelowApproval={fuseApprovalComposer}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
