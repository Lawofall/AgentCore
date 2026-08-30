import { ChatList } from "@/components/messages/ChatList";
import { ChatThread } from "@/components/messages/ChatThread";
import { ContactsDialog } from "@/components/messages/ContactsDialog";
import { NewChatDialog } from "@/components/messages/NewChatDialog";
import { ProductNoticeDetail } from "@/components/messages/ProductNoticeDetail";
import { UserProfileDialog } from "@/components/messages/UserProfileDialog";
import { EmptyHint } from "@/components/ui";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { useMessagingStore } from "@/stores/messaging";
import { Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

/**
 * 消息 page (找人 IM): wide = list + thread; narrow = list or thread
 * (route `/messages/:chatId` hides the tab bar). The route param `:chatId` is the source of truth for the open
 * chat — syncing it into the store (load history + mark read) mirrors how
 * ConversationPage drives the AI 对话 page (消息IM.md §六).
 *
 * Official product_notice detail: `#/messages/:chatId/notices/:noticeId`
 * replaces the thread pane (应用内详情，非外开浏览器).
 *
 * §9.4 surfaces (通讯录 / 资料卡 / 搜人) mount here so any pane can open them.
 */
export function MessagesPage() {
  const { chatId, noticeId } = useParams<{
    chatId: string;
    noticeId?: string;
  }>();
  const { isNarrow } = useNarrowLayoutState();
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [contactsOpen, setContactsOpen] = useState(false);
  const profileUserId = useMessagingStore((s) => s.profileUserId);
  const openProfile = useMessagingStore((s) => s.openProfile);
  const closeProfile = useMessagingStore((s) => s.closeProfile);
  const fetchFriendRequests = useMessagingStore((s) => s.fetchFriendRequests);

  useEffect(() => {
    const store = useMessagingStore.getState();
    if (!chatId) {
      store.setActiveChat(null);
      return;
    }
    if (chatId !== store.activeChatId) void store.openChat(chatId);
  }, [chatId]);

  // Hydrate request-box badge even before opening 通讯录.
  useEffect(() => {
    void fetchFriendRequests();
  }, [fetchFriendRequests]);

  const showThread = Boolean(chatId);

  return (
    <div className="flex h-full w-full">
      <ChatList
        activeChatId={chatId ?? null}
        onSelect={(id) => navigate(`/messages/${id}`)}
        onNewChat={() => setDialogOpen(true)}
        onOpenContacts={() => setContactsOpen(true)}
        className={
          isNarrow
            ? showThread
              ? "hidden"
              : "w-full min-w-0 border-r-0"
            : undefined
        }
      />
      {(!isNarrow || showThread) && (
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {chatId && noticeId ? (
            <ProductNoticeDetail chatId={chatId} noticeId={noticeId} />
          ) : chatId ? (
            <ChatThread chatId={chatId} />
          ) : (
            <EmptyHint
              inline
              icon={<Mail size={28} className="text-muted-foreground/40" />}
              title="选择一个会话，或发起新会话"
            />
          )}
        </section>
      )}
      <NewChatDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onOpenProfile={(userId) => {
          setDialogOpen(false);
          openProfile(userId);
        }}
      />
      <ContactsDialog
        open={contactsOpen}
        onClose={() => setContactsOpen(false)}
        onOpenProfile={(userId) => {
          openProfile(userId);
        }}
      />
      <UserProfileDialog
        userId={profileUserId}
        open={profileUserId !== null}
        onClose={closeProfile}
        onOpenChat={(id) => navigate(`/messages/${id}`)}
      />
    </div>
  );
}
