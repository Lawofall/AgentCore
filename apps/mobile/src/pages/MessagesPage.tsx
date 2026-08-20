import { getTokens } from "@/api/client";
import { type ChatSummary, chatTitle, listChats } from "@/api/messaging";
import { relativeTime } from "@/lib/time";
import { usePolling } from "@/lib/usePolling";
import { DisplayNameHint } from "@/pages/im/DisplayNameHint";
import { ImAvatar } from "@/pages/im/ImAvatar";
// 消息 list (人际 IM 会话列表) — the human↔human inbox, separate from the AI 对话 home.
//
// messages.py is REST-only, so the list POLLS (every 10s + on regaining visibility) for
// new chats / unread counts. Rows drill into a thread (/im/c/:id); the chat summary is
// passed via router state so the thread shows its title without a refetch.
import { SquarePen } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import "@/pages/im/im.css";

export function MessagesPage() {
  const navigate = useNavigate();
  const [chats, setChats] = useState<ChatSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  usePolling(async () => {
    try {
      setChats(await listChats());
      setError(null);
    } catch (e) {
      if (!getTokens()) {
        navigate("/login", { replace: true });
        return;
      }
      setError(e instanceof Error ? e.message : "加载会话失败");
    }
  }, 10_000);

  return (
    <div className="screen">
      <header className="bar">
        <span>消息</span>
        <button
          type="button"
          className="link icon-btn"
          aria-label="发起"
          onClick={() => navigate("/im/new")}
        >
          <SquarePen size={20} />
        </button>
      </header>

      <div className="list">
        <DisplayNameHint />
        {chats === null && !error && <p className="muted hint">加载中…</p>}
        {error && <p className="error hint">{error}</p>}
        {chats !== null && chats.length === 0 && !error && (
          <p className="muted hint">还没有会话。点右上角发起新聊天。</p>
        )}
        {chats?.map((chat) => (
          <ChatRow
            key={chat.id}
            chat={chat}
            onOpen={() => navigate(`/im/c/${chat.id}`, { state: { chat } })}
          />
        ))}
      </div>
    </div>
  );
}

function ChatRow({ chat, onOpen }: { chat: ChatSummary; onOpen: () => void }) {
  const title = chatTitle(chat);
  const unread = chat.unread ?? 0;
  const avatarUrl =
    chat.type === "dm"
      ? (chat.peer?.avatar_url ?? null)
      : (chat.avatar_url ?? null);
  return (
    <button type="button" className="im-row" onClick={onOpen}>
      {chat.type === "official" ? (
        <span className="im-avatar official">📣</span>
      ) : (
        <ImAvatar name={title} url={avatarUrl} />
      )}
      <span className="im-row-main">
        <span className="im-row-top">
          <span className="im-name">{title}</span>
          <span className="im-time">{relativeTime(chat.last_message_at)}</span>
        </span>
        <span className="im-row-bottom">
          <span className="im-preview">
            {chat.last_message_preview || "（无消息）"}
          </span>
          <span className="im-badges">
            {chat.state === "pending" && (
              <span className="im-pending">请求</span>
            )}
            {unread > 0 && (
              <span className="im-unread">{unread > 99 ? "99+" : unread}</span>
            )}
          </span>
        </span>
      </span>
    </button>
  );
}
