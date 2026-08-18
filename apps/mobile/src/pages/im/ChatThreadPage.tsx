import { type User, me } from "@/api/auth";
import { getTokens } from "@/api/client";
import {
  type ChatMessageDetail,
  type ChatParticipant,
  type ChatSummary,
  type SendContentType,
  type StoredAttachment,
  blockUser,
  chatTitle,
  fetchChatAttachmentBlob,
  isImageAttachment,
  leaveChat,
  listChats,
  listMembers,
  listMessages,
  markRead,
  sendMessage,
  uploadChatFile,
} from "@/api/messaging";
import { Modal } from "@/components/Modal";
import { useKeyboardInsetBridge } from "@/lib/keyboardInsets";
import { shareOrDownloadFile } from "@/lib/share";
import { clock } from "@/lib/time";
import { usePolling } from "@/lib/usePolling";
import { useStickScroll } from "@/lib/useStickScroll";
import { ChatImageGallery } from "@/pages/im/ChatImageGallery";
import { ImAvatar } from "@/pages/im/ImAvatar";
import {
  type MemberGovernanceBadge,
  type MessageReplyTo,
  bubbleAvatarUrl,
  buildReplySnapshot,
  memberGovernanceBadge,
  messageMentionsUser,
  splitContentByMentions,
} from "@/pages/im/chatDisplay";
import {
  ArrowDown,
  ChevronLeft,
  Crown,
  Loader2,
  MoreHorizontal,
  Send,
  Shield,
  UserCog,
} from "lucide-react";
// 消息线程 (/im/c/:chatId) — one human↔human thread. REST + polling (no SSE): the open
// thread refetches the most-recent page every 4s and merges by id, so sends from the peer
// appear within a cycle. IM list pagination is created_at ASC (page 1 = oldest), so the
// thread lands on the LAST page and pages backward via「加载更早」.
//
// Reply / @：展示服务端 `reply_to` + `mentions`；发送侧仅最小回复入口（无独立 @ 菜单 /
// presence 绿点 — 文档「不做手机端」）。
import {
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import "@/pages/im/im.css";

type ComposerReplyTarget = {
  messageId: string;
  snapshot: MessageReplyTo;
};

const PAGE_SIZE = 100;

/** Dedupe by id + sort ascending by created_at — stable across overlapping polled pages. */
function mergeMessages(
  prev: ChatMessageDetail[],
  incoming: ChatMessageDetail[],
): ChatMessageDetail[] {
  const byId = new Map(prev.map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ChatThreadPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { chatId } = useParams<{ chatId: string }>();
  const initialChat =
    (location.state as { chat?: ChatSummary } | null)?.chat ?? null;

  const [chat, setChat] = useState<ChatSummary | null>(initialChat);
  const [meUser, setMeUser] = useState<User | null>(null);
  const [members, setMembers] = useState<Map<string, ChatParticipant>>(
    new Map(),
  );
  const [messages, setMessages] = useState<ChatMessageDetail[]>([]);
  const [oldestPage, setOldestPage] = useState(1);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  // Files staged for the next send — uploaded to chat storage on send, then referenced as
  // StoredAttachments (unlike agent-chat, IM ships the file itself, images included).
  const [pending, setPending] = useState<File[]>([]);
  const [replyTarget, setReplyTarget] = useState<ComposerReplyTarget | null>(
    null,
  );

  const attachInputRef = useRef<HTMLInputElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const totalRef = useRef(0);
  const initedRef = useRef(false);
  const lastMarkedRef = useRef<string | null>(null);
  useKeyboardInsetBridge(shellRef);

  const lastMsg = messages.length > 0 ? messages[messages.length - 1] : null;
  const scrollContentKey = lastMsg
    ? `${lastMsg.id}-${messages.length}`
    : `empty-${messages.length}`;
  const { scrollRef, atBottom, jumpToBottom } = useStickScroll(
    scrollContentKey,
    chatId ?? null,
  );

  // My identity (mine vs theirs alignment + own avatar on sent bubbles).
  useEffect(() => {
    me()
      .then((u) => setMeUser(u))
      .catch(() => {
        if (!getTokens()) navigate("/login", { replace: true });
      });
  }, [navigate]);

  const myId = meUser?.id ?? null;

  // Chat summary fallback when opened via a deep link (no router state).
  useEffect(() => {
    if (chat || !chatId) return;
    listChats()
      .then((cs) => {
        const found = cs.find((c) => c.id === chatId);
        if (found) setChat(found);
      })
      .catch(() => {});
  }, [chat, chatId]);

  // Drop draft reply when switching threads.
  // biome-ignore lint/correctness/useExhaustiveDependencies: chatId is the intentional reset trigger
  useEffect(() => {
    setReplyTarget(null);
  }, [chatId]);

  useLayoutEffect(() => {
    const el = composerInputRef.current;
    if (!el) return;
    el.value = text;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [text]);

  // Group/official sender names (dm needs none — the only peer is the title).
  useEffect(() => {
    if (!chatId || !chat || chat.type === "dm") return;
    listMembers(chatId)
      .then((ms) => setMembers(new Map(ms.map((m) => [m.id, m]))))
      .catch(() => {});
  }, [chatId, chat]);

  usePolling(async () => {
    if (!chatId) return;
    try {
      if (!initedRef.current) {
        // Land on the most recent page: page 1 yields the total, then fetch the last page.
        const first = await listMessages(chatId, 1, PAGE_SIZE);
        totalRef.current = first.total;
        const lastPage = Math.max(1, Math.ceil(first.total / PAGE_SIZE));
        if (lastPage === 1) {
          setMessages(first.messages);
          setOldestPage(1);
        } else {
          const last = await listMessages(chatId, lastPage, PAGE_SIZE);
          totalRef.current = last.total;
          setMessages(last.messages);
          setOldestPage(lastPage);
        }
        initedRef.current = true;
        setLoaded(true);
      } else {
        const lastPage = Math.max(
          1,
          Math.ceil((totalRef.current || 1) / PAGE_SIZE),
        );
        const res = await listMessages(chatId, lastPage, PAGE_SIZE);
        totalRef.current = res.total;
        setMessages((prev) => mergeMessages(prev, res.messages));
      }
      setError(null);
    } catch (e) {
      if (!getTokens()) {
        navigate("/login", { replace: true });
        return;
      }
      if (!initedRef.current) {
        setError(e instanceof Error ? e.message : "加载消息失败");
        setLoaded(true);
      }
    }
  }, 4000);

  // Advance the read cursor when the newest message changes (drives unread counts).
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (chatId && last && lastMarkedRef.current !== last.id) {
      lastMarkedRef.current = last.id;
      void markRead(chatId, last.id).catch(() => {});
    }
  }, [messages, chatId]);

  async function loadOlder() {
    const target = oldestPage - 1;
    if (target < 1 || !chatId) return;
    try {
      const res = await listMessages(chatId, target, PAGE_SIZE);
      setMessages((prev) => mergeMessages(prev, res.messages));
      setOldestPage(target);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载更早失败");
    }
  }

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length > 0) setPending((prev) => [...prev, ...files]);
  }

  function removePending(idx: number) {
    setPending((prev) => prev.filter((_, i) => i !== idx));
  }

  // Send text and/or attachments. Files upload to chat storage FIRST (durable paths) — a
  // failed upload aborts the send and keeps the draft + files for retry. content_type is
  // derived (all-images → image, any non-image → file) to drive the peer's render.
  async function send() {
    const body = text.trim();
    const files = pending;
    if ((!body && files.length === 0) || !chatId || sending) return;
    setSending(true);
    setError(null);
    try {
      let attachments: StoredAttachment[] = [];
      if (files.length > 0) {
        attachments = await Promise.all(
          files.map(async (file) => {
            const path = `attachments/${crypto.randomUUID()}/${file.name}`;
            const res = await uploadChatFile(chatId, path, file);
            return {
              name: file.name,
              path: file.name,
              kind: "file",
              // IM 上传路径只存 blob，不内联正文 —— 一律 binary（与桌面端语义一致）。
              binary: true,
              truncated: false,
              workspace_path: res.path,
              size_bytes: res.size_bytes,
              thumb_path: res.thumb_path,
            } satisfies StoredAttachment;
          }),
        );
      }
      const contentType: SendContentType =
        attachments.length === 0
          ? "text"
          : attachments.every((a) => isImageAttachment(a.name))
            ? "image"
            : "file";
      const msg = await sendMessage(chatId, {
        content: body || undefined,
        contentType,
        attachments,
        clientMsgId: crypto.randomUUID(),
        replyToMessageId: replyTarget?.messageId,
      });
      setText("");
      setPending([]);
      setReplyTarget(null);
      totalRef.current += 1;
      jumpToBottom();
      setMessages((prev) => mergeMessages(prev, [msg]));
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSending(false);
    }
  }

  async function onBlock() {
    setSheetOpen(false);
    if (!chat?.peer) return;
    try {
      await blockUser(chat.peer.id);
      navigate("/im", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "拉黑失败");
    }
  }

  async function onLeave() {
    setSheetOpen(false);
    if (!chatId) return;
    try {
      await leaveChat(chatId);
      navigate("/im", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "退出会话失败");
    }
  }

  const title = chat ? chatTitle(chat) : "对话";
  const isDm = chat?.type === "dm";
  const isGroup = chat?.type === "group";
  const isOfficial = chat?.type === "official";
  const peerAvatarUrl = chat?.peer?.avatar_url ?? null;
  const myAvatarUrl = meUser?.avatar_url ?? null;
  const myAvatarName = meUser?.display_name || meUser?.username || "?";

  function resolveMentionName(userId: string): string | undefined {
    if (myId && userId === myId) return myAvatarName;
    const member = members.get(userId);
    if (member) return member.display_name || member.username;
    if (isDm && chat?.peer?.id === userId) {
      return chat.peer.display_name || chat.peer.username;
    }
    return undefined;
  }

  function startReply(message: ChatMessageDetail) {
    if (isOfficial) return;
    const mine = !!myId && message.sender_user_id === myId;
    const member = message.sender_user_id
      ? members.get(message.sender_user_id)
      : undefined;
    const senderName = mine
      ? myAvatarName
      : isDm
        ? title
        : member
          ? member.display_name || member.username
          : "成员";
    setReplyTarget({
      messageId: message.id,
      snapshot: buildReplySnapshot(message, senderName),
    });
    queueMicrotask(() => composerInputRef.current?.focus());
  }

  return (
    <div className="screen im-thread" ref={shellRef}>
      <header className="bar">
        <button
          type="button"
          className="link icon-btn"
          aria-label="返回"
          onClick={() => navigate("/im")}
        >
          <ChevronLeft size={20} />
        </button>
        <span className="bar-title">{title}</span>
        <div className="bar-right">
          <button
            type="button"
            className="link icon-btn"
            aria-label="更多"
            onClick={() => setSheetOpen(true)}
          >
            <MoreHorizontal size={20} />
          </button>
        </div>
      </header>

      <div className="messages-pane">
        <div className="messages" ref={scrollRef}>
          {!loaded && <p className="muted hint">加载中…</p>}
          {loaded && oldestPage > 1 && (
            <button
              type="button"
              className="load-older"
              onClick={() => void loadOlder()}
            >
              加载更早
            </button>
          )}
          {loaded && messages.length === 0 && !error && (
            <p className="muted hint">还没有消息，发送第一条吧。</p>
          )}
          {messages.map((m) => {
            const mine = !!myId && m.sender_user_id === myId;
            const member = m.sender_user_id
              ? members.get(m.sender_user_id)
              : undefined;
            const senderName = member
              ? member.display_name || member.username
              : undefined;
            const senderGovernance =
              isGroup && !mine && member ? memberGovernanceBadge(member) : null;
            const avatarName = mine
              ? myAvatarName
              : isDm
                ? title
                : (senderName ?? "?");
            const avatarUrl = bubbleAvatarUrl({
              mine,
              chatType: chat?.type,
              myAvatarUrl,
              peerAvatarUrl,
              memberAvatarUrl: member?.avatar_url,
              chatAvatarUrl: chat?.avatar_url,
            });
            return (
              <MessageRow
                key={m.id}
                message={m}
                mine={mine}
                chatId={chatId ?? ""}
                isGroup={isGroup}
                senderName={senderName}
                senderGovernance={senderGovernance}
                avatarName={avatarName}
                avatarUrl={avatarUrl}
                myUserId={myId}
                resolveMentionName={resolveMentionName}
                canReply={!isOfficial}
                onReply={() => startReply(m)}
              />
            );
          })}
        </div>
        {!atBottom && messages.length > 0 ? (
          <button
            type="button"
            className="jump-bottom"
            onClick={jumpToBottom}
            aria-label="回到底部"
          >
            <ArrowDown size={14} aria-hidden />
            回到底部
          </button>
        ) : null}
      </div>

      {chat?.state === "pending" && (
        <div className="im-pending-note">
          陌生人的消息请求：回复即表示接受。
        </div>
      )}

      {error && <div className="error bar">{error}</div>}

      {!isOfficial && pending.length > 0 && (
        <div className="attach-tray">
          {pending.map((f, i) => (
            <PendingChip
              key={`${f.name}-${i}`}
              file={f}
              onRemove={() => removePending(i)}
            />
          ))}
        </div>
      )}

      {!isOfficial && replyTarget && (
        <div className="im-reply-bar">
          <div className="im-reply-bar-main">
            <span className="im-reply-bar-label">
              回复 {replyTarget.snapshot.sender_display_name}
            </span>
            <span className="im-reply-bar-preview">
              {replyTarget.snapshot.body_preview}
            </span>
          </div>
          <button
            type="button"
            className="im-reply-bar-x"
            onClick={() => setReplyTarget(null)}
            aria-label="取消回复"
          >
            ×
          </button>
        </div>
      )}

      {!isOfficial && (
        <div className="composer">
          <input
            ref={attachInputRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={onPickFiles}
          />
          <button
            type="button"
            className="attach-btn"
            onClick={() => attachInputRef.current?.click()}
            disabled={sending}
            aria-label="添加附件"
          >
            ＋
          </button>
          <textarea
            ref={composerInputRef}
            className="composer-input"
            rows={1}
            value={text}
            placeholder={replyTarget ? "输入回复…" : "发送消息…"}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.shiftKey) return;
              if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229)
                return;
              e.preventDefault();
              void send();
            }}
          />
          <button
            type="button"
            className="send-btn"
            onClick={() => void send()}
            disabled={(!text.trim() && pending.length === 0) || sending}
            aria-label={sending ? "发送中…" : "发送"}
            title={sending ? "发送中…" : "发送"}
          >
            {sending ? (
              <Loader2 size={18} className="voice-spin" aria-hidden />
            ) : (
              <Send size={18} aria-hidden />
            )}
          </button>
        </div>
      )}

      {sheetOpen && (
        <Modal
          className="sheet"
          onClose={() => setSheetOpen(false)}
          label={title}
        >
          <div className="sheet-title">{title}</div>
          {isDm ? (
            <button
              type="button"
              className="sheet-item sheet-danger"
              onClick={() => void onBlock()}
            >
              拉黑此人
            </button>
          ) : !isOfficial ? (
            <button
              type="button"
              className="sheet-item sheet-danger"
              onClick={() => void onLeave()}
            >
              退出会话
            </button>
          ) : null}
          <button
            type="button"
            className="sheet-item sheet-cancel"
            onClick={() => setSheetOpen(false)}
          >
            取消
          </button>
        </Modal>
      )}
    </div>
  );
}

function SenderRoleMark({ badge }: { badge: MemberGovernanceBadge }) {
  const Icon =
    badge.kind === "platform"
      ? UserCog
      : badge.kind === "owner"
        ? Crown
        : Shield;
  return (
    <span
      className={`im-role ${badge.kind === "platform" ? "im-role-platform" : "im-role-group"}`}
      aria-label={badge.label}
    >
      <Icon size={12} aria-hidden />
      {badge.shortLabel}
    </span>
  );
}

function MessageRow({
  message,
  mine,
  chatId,
  isGroup,
  senderName,
  senderGovernance,
  avatarName,
  avatarUrl,
  myUserId,
  resolveMentionName,
  canReply,
  onReply,
}: {
  message: ChatMessageDetail;
  mine: boolean;
  chatId: string;
  isGroup: boolean;
  senderName?: string;
  senderGovernance?: MemberGovernanceBadge | null;
  avatarName: string;
  avatarUrl?: string | null;
  myUserId: string | null;
  resolveMentionName: (userId: string) => string | undefined;
  canReply: boolean;
  onReply: () => void;
}) {
  // Server-minted official notices / system cards render centered, not as a bubble.
  if (
    message.content_type === "system_card" ||
    message.sender_type === "official"
  ) {
    return <div className="im-system">{message.content || "（系统消息）"}</div>;
  }

  const attachments = message.attachments ?? [];
  const images = attachments.filter(
    (a) => a.kind !== "dir" && a.workspace_path && isImageAttachment(a.name),
  );
  const files = attachments.filter((a) => !images.includes(a));
  const reply = message.reply_to ?? null;
  const hasText = Boolean(message.content);
  const mentionedMe = !mine && messageMentionsUser(message, myUserId ?? null);
  const bubbleClass = ["im-bubble", mentionedMe ? "im-bubble-mentioned" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={`im-msg ${mine ? "mine" : "theirs"}`}>
      <div className="im-msg-row">
        <ImAvatar
          name={avatarName}
          url={avatarUrl}
          className="im-avatar im-msg-avatar"
        />
        <div className="im-msg-body">
          {!mine && isGroup && senderName && (
            <span className="im-sender-row">
              <span className="im-sender">{senderName}</span>
              {senderGovernance && <SenderRoleMark badge={senderGovernance} />}
            </span>
          )}
          {(hasText || reply) && (
            <div className={bubbleClass}>
              {reply && <ReplyQuote reply={reply} mine={mine} />}
              {hasText && message.content ? (
                <MentionBody
                  content={message.content}
                  mentions={message.mentions}
                  mine={mine}
                  myUserId={myUserId}
                  resolveMentionName={resolveMentionName}
                />
              ) : null}
            </div>
          )}
          {images.length > 0 && (
            <ChatImageGallery chatId={chatId} images={images} />
          )}
          {files.length > 0 && (
            <div className="im-attachments">
              {files.map((a, i) => (
                <FileAttachmentChip
                  key={a.workspace_path ?? `${a.name}-${i}`}
                  chatId={chatId}
                  attachment={a}
                />
              ))}
            </div>
          )}
          <div className="im-msg-meta">
            <span className="im-msg-time">{clock(message.created_at)}</span>
            {canReply && (
              <button
                type="button"
                className="im-reply-action"
                onClick={onReply}
              >
                回复
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ReplyQuote({
  reply,
  mine,
}: {
  reply: MessageReplyTo;
  mine: boolean;
}) {
  return (
    <div
      className={`im-reply-quote${mine ? " mine" : ""}`}
      aria-label={`回复 ${reply.sender_display_name}：${reply.body_preview}`}
    >
      <span className="im-reply-quote-name">{reply.sender_display_name}</span>
      <span className="im-reply-quote-body">{reply.body_preview}</span>
    </div>
  );
}

function MentionBody({
  content,
  mentions,
  mine,
  myUserId,
  resolveMentionName,
}: {
  content: string;
  mentions: ChatMessageDetail["mentions"];
  mine: boolean;
  myUserId: string | null;
  resolveMentionName: (userId: string) => string | undefined;
}): ReactNode {
  const segments = splitContentByMentions(
    content,
    mentions,
    resolveMentionName,
    myUserId,
  );
  let offset = 0;
  return (
    <>
      {segments.map((seg) => {
        const key =
          seg.type === "text"
            ? `t:${offset}:${seg.text}`
            : `m:${offset}:${seg.text}`;
        offset += seg.text.length;
        if (seg.type === "text") {
          return <span key={key}>{seg.text}</span>;
        }
        const cls = [
          "im-mention",
          seg.self ? "im-mention-self" : "",
          mine ? "mine" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <span key={key} className={cls}>
            {seg.text}
          </span>
        );
      })}
    </>
  );
}

/** Non-image attachment chip: tap shares/saves via the OS sheet (or download fallback). */
function FileAttachmentChip({
  chatId,
  attachment,
}: {
  chatId: string;
  attachment: StoredAttachment;
}) {
  const path = attachment.workspace_path ?? null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open() {
    if (!path || busy) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await fetchChatAttachmentBlob(chatId, path);
      await shareOrDownloadFile(blob, attachment.name, blob.type);
    } catch (e) {
      setError(e instanceof Error ? e.message : "下载附件失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="im-attach-file-wrap">
      <button
        type="button"
        className="im-attach-file"
        onClick={() => void open()}
        disabled={!path || busy}
      >
        <span aria-hidden>📎</span>
        <span className="im-attach-name">{attachment.name}</span>
        {attachment.size_bytes != null && (
          <span className="im-attach-size">
            {formatSize(attachment.size_bytes)}
          </span>
        )}
      </button>
      {error && <span className="im-attach-error">{error}</span>}
    </div>
  );
}

/** Composer pending file chip — images show a local objectURL thumbnail. */
function PendingChip({
  file,
  onRemove,
}: {
  file: File;
  onRemove: () => void;
}) {
  const image = file.type.startsWith("image/") || isImageAttachment(file.name);
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!image) return;
    const url = URL.createObjectURL(file);
    setThumbUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file, image]);

  return (
    <span className={`attach-chip${image && thumbUrl ? " has-thumb" : ""}`}>
      {image && thumbUrl ? (
        <img className="attach-chip-thumb" src={thumbUrl} alt="" />
      ) : (
        <span aria-hidden>📎</span>
      )}
      <span className="attach-chip-name">{file.name}</span>
      <span className="attach-chip-trunc">{formatSize(file.size)}</span>
      <button
        type="button"
        className="attach-chip-x"
        onClick={onRemove}
        aria-label="移除附件"
      >
        ×
      </button>
    </span>
  );
}
