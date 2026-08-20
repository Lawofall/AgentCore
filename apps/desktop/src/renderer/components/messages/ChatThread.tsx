import { Button, IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  IM_SESSION_COLUMN_CLASS,
  buildImThreadItems,
} from "@/lib/imMessageLayout";
import { notifyActionError, notifyInfo } from "@/lib/toast";
import { useStickToBottom } from "@/lib/useStickToBottom";
import type { ChatMessageDetail } from "@/services/messaging";
import { useAuthStore } from "@/stores/auth";
import {
  useActiveChat,
  useActiveMessages,
  useChatMembers,
  useMessagingStore,
} from "@/stores/messaging";
import { ArrowDown, BadgeCheck, Info } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatBubble } from "./ChatBubble";
import {
  ChatComposer,
  type ComposerEditTarget,
  type ComposerReplyTarget,
} from "./ChatComposer";
import { ChatDateDivider } from "./ChatDateDivider";
import { GroupInfoDialog } from "./GroupInfoDialog";
import { PresenceAvatar } from "./PresenceAvatar";
import {
  avatarInitial,
  bubbleAvatarUrl,
  buildReplySnapshot,
  chatCircleAvatarUrl,
  chatDisplayName,
  isGroupModeratorRole,
  memberGovernanceBadge,
} from "./chatDisplay";

interface Props {
  chatId: string;
}

/** Right pane: the active chat's message thread + composer. */
export function ChatThread({ chatId }: Props) {
  const chat = useActiveChat();
  const messages = useActiveMessages();
  const members = useChatMembers(chatId);
  const loading = useMessagingStore((s) => s.loadingMessages[chatId] ?? false);
  const loadingOlder = useMessagingStore(
    (s) => s.loadingOlderMessages[chatId] ?? false,
  );
  const hasMoreOlder = useMessagingStore(
    (s) => s.messagesMetaByChat[chatId]?.hasMoreOlder ?? false,
  );
  const loadMembers = useMessagingStore((s) => s.loadMembers);
  const loadOlderMessages = useMessagingStore((s) => s.loadOlderMessages);
  const openProfile = useMessagingStore((s) => s.openProfile);
  const recallMessage = useMessagingStore((s) => s.recallMessage);
  const user = useAuthStore((s) => s.user);
  const myId = user?.id ?? null;
  const isAdmin = user?.role === "admin";

  const isGroup = chat?.type === "group";
  const isOfficial = chat?.type === "official";
  const showInfo = isGroup || isOfficial;
  const isGroupModerator = useMemo(() => {
    if (!isGroup || !myId) return false;
    const me = members.find((m) => m.id === myId);
    return isGroupModeratorRole(me?.group_role);
  }, [isGroup, members, myId]);
  const [infoOpen, setInfoOpen] = useState(false);
  const [replyTarget, setReplyTarget] = useState<ComposerReplyTarget | null>(
    null,
  );
  const [editTarget, setEditTarget] = useState<ComposerEditTarget | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);

  const recalledIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of messages) {
      if (m.recalled_at) ids.add(m.id);
    }
    return ids;
  }, [messages]);

  // Group threads label each message with its sender, so they need the roster;
  // load it when a group opens (dms render the single peer's name in the header).
  useEffect(() => {
    if (isGroup) void loadMembers(chatId);
  }, [isGroup, chatId, loadMembers]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: chatId resets reply/edit draft on switch.
  useEffect(() => {
    setReplyTarget(null);
    setEditTarget(null);
    setHighlightId(null);
  }, [chatId]);

  useEffect(() => {
    if (!highlightId) return;
    const t = window.setTimeout(() => setHighlightId(null), 1600);
    return () => window.clearTimeout(t);
  }, [highlightId]);

  const nameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of members) map.set(m.id, m.display_name || m.username);
    if (chat?.peer) {
      map.set(chat.peer.id, chat.peer.display_name || chat.peer.username);
    }
    if (myId) {
      map.set(myId, user?.displayName || user?.username || "我");
    }
    return map;
  }, [members, chat?.peer, myId, user?.displayName, user?.username]);

  const memberById = useMemo(() => {
    const map = new Map<string, (typeof members)[number]>();
    for (const m of members) map.set(m.id, m);
    return map;
  }, [members]);

  const threadItems = useMemo(() => buildImThreadItems(messages), [messages]);

  const { scrollRef, contentRef, atBottom, jumpToBottom } =
    useStickToBottom(chatId);

  const name = chat ? chatDisplayName(chat) : "";
  const memberCount = isGroup && members.length > 0 ? members.length : null;
  const onlineCount = isGroup ? members.filter((m) => m.online).length : null;
  const peerOnline = chat?.type === "dm" ? !!chat.peer?.online : false;
  const hasMessages = messages.length > 0;
  // viewer.state === pending means someone opened this dm with us and we have
  // not replied yet — a message request (replying accepts it, 消息IM.md §五).
  const isRequest = chat?.state === "pending";

  let headerSubtitle: string | null = null;
  if (chat?.type === "dm") {
    headerSubtitle = peerOnline ? "在线" : "离线";
  } else if (isOfficial) {
    headerSubtitle = "官方广播";
  } else if (isGroup && members.length > 0) {
    headerSubtitle = `${onlineCount} 人在线`;
  } else if (memberCount) {
    headerSubtitle = `${memberCount} 名成员`;
  }

  const resolveSenderLabel = useCallback(
    (message: ChatMessageDetail): string => {
      if (myId && message.sender_user_id === myId) {
        return user?.displayName || user?.username || "我";
      }
      if (isGroup && message.sender_user_id) {
        return nameById.get(message.sender_user_id) ?? "成员";
      }
      return name || "成员";
    },
    [isGroup, myId, name, nameById, user?.displayName, user?.username],
  );

  const handleReply = useCallback(
    (message: ChatMessageDetail) => {
      if (message.recalled_at) return;
      setEditTarget(null);
      setReplyTarget({
        messageId: message.id,
        snapshot: buildReplySnapshot(message, resolveSenderLabel(message)),
      });
    },
    [resolveSenderLabel],
  );

  const handleEdit = useCallback((message: ChatMessageDetail) => {
    if (message.recalled_at) return;
    setReplyTarget(null);
    setEditTarget({
      messageId: message.id,
      content: message.content ?? "",
    });
  }, []);

  const handleRecall = useCallback(
    (message: ChatMessageDetail) => {
      void recallMessage(chatId, message.id).catch((e) =>
        notifyActionError("撤回失败", e),
      );
    },
    [chatId, recallMessage],
  );

  const handleScrollToReply = useCallback(
    (messageId: string) => {
      const el = scrollRef.current?.querySelector(
        `[data-message-id="${CSS.escape(messageId)}"]`,
      );
      if (!(el instanceof HTMLElement)) {
        notifyInfo("原消息不在当前已加载范围");
        return;
      }
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightId(messageId);
    },
    [scrollRef],
  );

  async function handleLoadOlder() {
    const el = scrollRef.current;
    const prevHeight = el?.scrollHeight ?? 0;
    const prevTop = el?.scrollTop ?? 0;
    await loadOlderMessages(chatId);
    requestAnimationFrame(() => {
      const container = scrollRef.current;
      if (!container) return;
      container.scrollTop = container.scrollHeight - prevHeight + prevTop;
    });
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        {chat?.type === "dm" && chat.peer?.id ? (
          <button
            type="button"
            onClick={() => {
              const peerId = chat.peer?.id;
              if (peerId) openProfile(peerId);
            }}
            className="flex min-w-0 flex-1 items-center gap-2 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`查看 ${name} 的资料`}
          >
            <PresenceAvatar
              label={avatarInitial(name || "?")}
              url={chatCircleAvatarUrl(chat)}
              sizeClass="size-7"
              textClass="text-xs"
              online={peerOnline}
            />
            <span className="flex min-w-0 flex-col">
              <span className="truncate text-base font-medium text-foreground">
                {name}
              </span>
              {headerSubtitle && (
                <span className="text-xs text-muted-foreground">
                  {headerSubtitle}
                </span>
              )}
            </span>
          </button>
        ) : (
          <>
            <PresenceAvatar
              label={avatarInitial(name || "?")}
              url={chat ? chatCircleAvatarUrl(chat) : null}
              sizeClass="size-7"
              textClass="text-xs"
              online={false}
            />
            <span className="flex min-w-0 flex-col">
              <span className="flex items-center gap-1 truncate text-base font-medium text-foreground">
                {isOfficial && (
                  <BadgeCheck size={14} className="shrink-0 text-primary" />
                )}
                <span className="min-w-0 truncate">{name}</span>
              </span>
              {headerSubtitle && (
                <span className="text-xs text-muted-foreground">
                  {headerSubtitle}
                </span>
              )}
            </span>
          </>
        )}
        {showInfo && (
          <SimpleTooltip label={isOfficial ? "会话设置" : "群信息"}>
            <IconButton
              size="md"
              onClick={() => setInfoOpen(true)}
              aria-label={isOfficial ? "会话设置" : "群信息"}
              className="ml-auto shrink-0"
            >
              <Info size={18} />
            </IconButton>
          </SimpleTooltip>
        )}
      </div>

      {isRequest && (
        <div className={`${IM_SESSION_COLUMN_CLASS} px-4 pt-3`}>
          <div className="rounded-lg border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
            这是一条消息请求，回复即代表接受。
          </div>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} className="h-full overflow-y-auto">
          {/* min-h (never h) so the empty placeholder can center against the
              viewport while this box still GROWS with the transcript — a pinned
              height would freeze the size ResizeObserver watches and kill stick-to-bottom. */}
          <div
            ref={contentRef}
            className={`${IM_SESSION_COLUMN_CLASS} flex min-h-full flex-col`}
          >
            {hasMessages ? (
              <div className="flex flex-col gap-2 px-4 py-4">
                {hasMoreOlder && (
                  <div className="flex justify-center pb-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={loadingOlder}
                      onClick={() => void handleLoadOlder()}
                      className="text-xs text-muted-foreground"
                    >
                      {loadingOlder ? "加载中…" : "加载更早消息"}
                    </Button>
                  </div>
                )}
                {threadItems.map((item) => {
                  if (item.type === "date_divider") {
                    return (
                      <ChatDateDivider key={item.key} label={item.label} />
                    );
                  }
                  const m = item.message;
                  const mine = !!myId && m.sender_user_id === myId;
                  const peerName = name || "成员";
                  const senderName =
                    isGroup && !mine && m.sender_user_id
                      ? (nameById.get(m.sender_user_id) ?? "成员")
                      : undefined;
                  const avatarName = mine
                    ? user?.displayName || user?.username || "?"
                    : isGroup
                      ? (senderName ?? "成员")
                      : peerName;
                  const senderMember =
                    isGroup && m.sender_user_id
                      ? memberById.get(m.sender_user_id)
                      : undefined;
                  const senderAvatarUrl = bubbleAvatarUrl({
                    mine,
                    chatType: chat?.type,
                    myAvatarUrl: user?.avatarUrl,
                    peerAvatarUrl: chat?.peer?.avatar_url,
                    memberAvatarUrl: senderMember?.avatar_url,
                    chatAvatarUrl: chat?.avatar_url,
                  });
                  const senderGovernance =
                    isGroup && !mine && senderMember
                      ? memberGovernanceBadge(senderMember)
                      : null;
                  return (
                    <ChatBubble
                      key={item.key}
                      message={m}
                      mine={mine}
                      senderName={senderName}
                      senderGovernance={senderGovernance}
                      avatarName={avatarName}
                      senderAvatarUrl={senderAvatarUrl}
                      layout={item.layout}
                      highlighted={highlightId === m.id}
                      myUserId={myId}
                      isAdmin={isAdmin}
                      isGroupModerator={isGroupModerator}
                      chatType={chat?.type}
                      resolveMentionName={(id) => nameById.get(id)}
                      onReply={
                        isOfficial || m.recalled_at ? undefined : handleReply
                      }
                      onRecall={handleRecall}
                      onEdit={isOfficial ? undefined : handleEdit}
                      onScrollToReply={handleScrollToReply}
                      replyTargetRecalled={
                        !!m.reply_to_message_id &&
                        recalledIds.has(m.reply_to_message_id)
                      }
                      onAvatarClick={isGroup ? openProfile : undefined}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="flex flex-1 items-center justify-center">
                <p className="text-sm text-muted-foreground">
                  {loading
                    ? "加载中…"
                    : isOfficial
                      ? "暂无公告"
                      : "还没有消息，发送第一条消息吧"}
                </p>
              </div>
            )}
          </div>
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

      {!isOfficial && (
        <div className={IM_SESSION_COLUMN_CLASS}>
          <ChatComposer
            chatId={chatId}
            replyTarget={replyTarget}
            onClearReply={() => setReplyTarget(null)}
            editTarget={editTarget}
            onClearEdit={() => setEditTarget(null)}
          />
        </div>
      )}

      {showInfo && (
        <GroupInfoDialog
          chatId={chatId}
          open={infoOpen}
          onClose={() => setInfoOpen(false)}
        />
      )}
    </div>
  );
}
