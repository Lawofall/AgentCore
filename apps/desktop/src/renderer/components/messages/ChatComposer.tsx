import { IconButton } from "@/components/ui";
import { noticeChipNeutral } from "@/components/ui/tone-presets";
import { collectClipboardFiles } from "@/lib/clipboardFiles";
import { cn } from "@/lib/utils";
import {
  type ChatMention,
  type MessageReplyTo,
  isImageAttachment,
  messagingErrorMessage,
} from "@/services/messaging";
import { useAuthStore } from "@/stores/auth";
import {
  useActiveChat,
  useChatMembers,
  useMessagingStore,
} from "@/stores/messaging";
import {
  AlertTriangle,
  FileText,
  Loader2,
  Paperclip,
  Send,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatMentionMenu, type ChatMentionMenuItem } from "./ChatMentionMenu";
import {
  EVERYONE_MENTION_LABEL,
  canActAsGroupModerator,
  filterMentionsInContent,
  findImMentionDraft,
  mentionRoleSubtitle,
} from "./chatDisplay";

/** Composer-local reply target (id + snapshot for the quote bar / send). */
export interface ComposerReplyTarget {
  messageId: string;
  snapshot: MessageReplyTo;
}

/** Composer-local edit target (id + body prefilled into the textarea). */
export interface ComposerEditTarget {
  messageId: string;
  content: string;
}

interface Props {
  chatId: string;
  /** Active reply target shown above the input; null when not replying. */
  replyTarget?: ComposerReplyTarget | null;
  /** Clear the reply target (cancel button / after successful send). */
  onClearReply?: () => void;
  /** Active edit target; when set, composer saves via edit API instead of send. */
  editTarget?: ComposerEditTarget | null;
  /** Clear the edit target (cancel / after successful save). */
  onClearEdit?: () => void;
}

/** A file staged for sending, with an object URL preview for images. */
interface Pending {
  id: string;
  file: File;
  previewUrl?: string;
}

const MAX_ATTACHMENTS = 9;
const MAX_FILE_BYTES = 50 * 1024 * 1024; // mirrors workspace_upload_max_bytes

/**
 * IM message composer: an auto-growing textarea + attachments (图/文件). Enter
 * sends, Shift+Enter inserts a newline. Files can be added via the paperclip, by
 * pasting an image, or by dragging files onto the composer; each shows a pending
 * chip/thumbnail until sent.
 *
 * `@` opens a member mention menu (IM-only — not the AI-chat file @ menu).
 * Selecting a row inserts a visible `@显示名` / `@所有人` and records structured
 * `mentions` for the send payload.
 *
 * Sending is optimistic in the store (it uploads files first, then appends a
 * local twin and swaps it for the stored message). This owns the draft + staged
 * files and surfaces both local validation errors and the store's send error
 * (always {@link noticeChipNeutral} — IM has no 去配置 action).
 * An optional reply target renders a cancelable quote bar above the input.
 * An optional edit target prefills the textarea and saves via PATCH (no attachments).
 */
export function ChatComposer({
  chatId,
  replyTarget,
  onClearReply,
  editTarget,
  onClearEdit,
}: Props) {
  const [value, setValue] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [pendingMentions, setPendingMentions] = useState<ChatMention[]>([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionRange, setMentionRange] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const [mentionActive, setMentionActive] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sendError = useMessagingStore((s) => s.sendError);
  const clearSendError = useMessagingStore((s) => s.clearSendError);
  const loadMembers = useMessagingStore((s) => s.loadMembers);
  const chat = useActiveChat();
  const members = useChatMembers(chatId);
  const user = useAuthStore((s) => s.user);
  const myId = user?.id ?? null;
  const isPlatformAdmin = user?.role === "admin";
  const isGroup = chat?.type === "group";
  const isEditing = Boolean(editTarget);
  const myGroupRole = useMemo(
    () => (myId ? members.find((m) => m.id === myId)?.group_role : undefined),
    [members, myId],
  );
  const canMentionEveryone = canActAsGroupModerator(
    isPlatformAdmin,
    myGroupRole,
  );

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: value is an intentional re-run key — re-measure on every input change.
  useEffect(() => {
    adjustHeight();
  }, [value, adjustHeight]);

  // Revoke any image preview object URLs when the staged set changes / unmounts.
  useEffect(() => {
    return () => {
      for (const p of pending) {
        if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
      }
    };
  }, [pending]);

  // Switching chats starts a fresh draft and drops any stale errors / staged files.
  // biome-ignore lint/correctness/useExhaustiveDependencies: chatId is an intentional re-run key — reset the composer whenever the active chat changes.
  useEffect(() => {
    setValue("");
    setPending([]);
    setPendingMentions([]);
    setMentionOpen(false);
    setMentionQuery("");
    setMentionRange(null);
    setLocalError(null);
    clearSendError();
  }, [chatId, clearSendError]);

  // Focus the textarea when the user picks a message to reply to / edit.
  useEffect(() => {
    if (replyTarget || editTarget) textareaRef.current?.focus();
  }, [replyTarget, editTarget]);

  // Prefill + clear staged files when entering edit mode.
  useEffect(() => {
    if (!editTarget) return;
    setValue(editTarget.content);
    setPending((prev) => {
      for (const p of prev) {
        if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
      }
      return [];
    });
    setPendingMentions([]);
    setMentionOpen(false);
    setMentionQuery("");
    setMentionRange(null);
    setLocalError(null);
    clearSendError();
  }, [editTarget, clearSendError]);

  // Groups need a roster for the @ menu; DMs use the peer on ChatSummary.
  useEffect(() => {
    if (isGroup) void loadMembers(chatId);
  }, [isGroup, chatId, loadMembers]);

  const resolveUserName = useCallback(
    (userId: string): string | undefined => {
      if (myId && userId === myId) {
        return user?.displayName || user?.username;
      }
      const fromRoster = members.find((m) => m.id === userId);
      if (fromRoster) return fromRoster.display_name || fromRoster.username;
      if (chat?.peer?.id === userId) {
        return chat.peer.display_name || chat.peer.username;
      }
      return undefined;
    },
    [chat?.peer, members, myId, user?.displayName, user?.username],
  );

  const mentionItems = useMemo((): ChatMentionMenuItem[] => {
    const q = mentionQuery.trim().toLowerCase();
    const items: ChatMentionMenuItem[] = [];

    if (
      isGroup &&
      canMentionEveryone &&
      (!q || EVERYONE_MENTION_LABEL.includes(q) || "everyone".includes(q))
    ) {
      items.push({ kind: "everyone", label: EVERYONE_MENTION_LABEL });
    }

    const candidates = isGroup
      ? members.filter((m) => m.id !== myId)
      : chat?.peer && chat.peer.id !== myId
        ? [chat.peer]
        : [];

    for (const m of candidates) {
      const label = m.display_name || m.username;
      const hay = `${label} ${m.username}`.toLowerCase();
      if (q && !hay.includes(q)) continue;
      items.push({
        kind: "user",
        userId: m.id,
        label,
        subtitle: isGroup
          ? mentionRoleSubtitle(m)
          : m.username
            ? `@${m.username}`
            : undefined,
        avatarUrl: m.avatar_url ?? null,
      });
    }
    return items;
  }, [canMentionEveryone, chat?.peer, isGroup, members, mentionQuery, myId]);

  // Keep active index in range when the filtered list shrinks.
  useEffect(() => {
    setMentionActive((i) =>
      mentionItems.length === 0 ? 0 : Math.min(i, mentionItems.length - 1),
    );
  }, [mentionItems.length]);

  const syncMentionDraft = useCallback((text: string, caret: number) => {
    const draft = findImMentionDraft(text, caret);
    if (!draft) {
      setMentionOpen(false);
      setMentionQuery("");
      setMentionRange(null);
      return;
    }
    setMentionOpen(true);
    setMentionQuery(draft.query);
    setMentionRange({ start: draft.start, end: draft.end });
    setMentionActive(0);
  }, []);

  const closeMentionMenu = useCallback(() => {
    setMentionOpen(false);
    setMentionQuery("");
    setMentionRange(null);
  }, []);

  const insertMention = useCallback(
    (item: ChatMentionMenuItem) => {
      const el = textareaRef.current;
      if (!el || !mentionRange) return;
      const token =
        item.kind === "everyone"
          ? `@${EVERYONE_MENTION_LABEL}`
          : `@${item.label}`;
      const before = value.slice(0, mentionRange.start);
      const after = value.slice(mentionRange.end);
      const next = `${before}${token} ${after}`;
      const caret = before.length + token.length + 1;
      setValue(next);
      setPendingMentions((prev) => {
        const nextMention: ChatMention =
          item.kind === "everyone"
            ? { kind: "everyone" }
            : { kind: "user", user_id: item.userId };
        const key =
          nextMention.kind === "everyone"
            ? "everyone"
            : `user:${nextMention.user_id}`;
        if (
          prev.some((m) =>
            m.kind === "everyone"
              ? key === "everyone"
              : key === `user:${m.user_id}`,
          )
        ) {
          return prev;
        }
        return [...prev, nextMention];
      });
      closeMentionMenu();
      requestAnimationFrame(() => {
        el.focus();
        el.setSelectionRange(caret, caret);
        adjustHeight();
      });
    },
    [adjustHeight, closeMentionMenu, mentionRange, value],
  );

  const addFiles = useCallback(
    (incoming: File[]) => {
      if (isEditing) return;
      if (incoming.length === 0) return;
      setLocalError(null);
      setPending((prev) => {
        const next = [...prev];
        for (const file of incoming) {
          if (next.length >= MAX_ATTACHMENTS) {
            setLocalError(`最多只能添加 ${MAX_ATTACHMENTS} 个附件`);
            break;
          }
          if (file.size > MAX_FILE_BYTES) {
            setLocalError(
              `「${file.name}」超过 ${Math.round(MAX_FILE_BYTES / (1024 * 1024))} MB 上限`,
            );
            continue;
          }
          next.push({
            id: crypto.randomUUID(),
            file,
            previewUrl: isImageAttachment(file.name)
              ? URL.createObjectURL(file)
              : undefined,
          });
        }
        return next;
      });
    },
    [isEditing],
  );

  const removePending = useCallback((id: string) => {
    setPending((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((p) => p.id !== id);
    });
  }, []);

  const handleSend = useCallback(() => {
    const text = value.trim();
    if (sending) return;
    if (isEditing && editTarget) {
      if (!text) return;
      if (text === editTarget.content.trim()) {
        onClearEdit?.();
        setValue("");
        return;
      }
      setSending(true);
      setLocalError(null);
      clearSendError();
      void (async () => {
        try {
          await useMessagingStore
            .getState()
            .editMessage(chatId, editTarget.messageId, text);
          setValue("");
          onClearEdit?.();
        } catch (err) {
          setLocalError(messagingErrorMessage(err, "编辑失败，请重试"));
        } finally {
          setSending(false);
        }
      })();
      return;
    }
    if (!text && pending.length === 0) return;
    const files = pending.map((p) => p.file);
    const reply = replyTarget
      ? { messageId: replyTarget.messageId, snapshot: replyTarget.snapshot }
      : null;
    const mentions = filterMentionsInContent(
      text,
      pendingMentions,
      resolveUserName,
    );
    setValue("");
    setPendingMentions([]);
    closeMentionMenu();
    setSending(true);
    void (async () => {
      await useMessagingStore
        .getState()
        .sendMessage(chatId, text, files, reply, mentions);
      setSending(false);
      // Keep the staged files / reply draft if the send failed so the user can
      // retry; clear them on success.
      if (!useMessagingStore.getState().sendError) {
        for (const p of pending) {
          if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
        }
        setPending([]);
        onClearReply?.();
      }
    })();
  }, [
    value,
    pending,
    sending,
    chatId,
    replyTarget,
    onClearReply,
    editTarget,
    isEditing,
    onClearEdit,
    pendingMentions,
    resolveUserName,
    closeMentionMenu,
    clearSendError,
  ]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;
    if (mentionOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionActive((i) =>
          mentionItems.length === 0 ? 0 : (i + 1) % mentionItems.length,
        );
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionActive((i) =>
          mentionItems.length === 0
            ? 0
            : (i - 1 + mentionItems.length) % mentionItems.length,
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        const item = mentionItems[mentionActive];
        if (item) {
          e.preventDefault();
          insertMention(item);
          return;
        }
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeMentionMenu();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const files = collectClipboardFiles(e.clipboardData);
    if (files.length > 0) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) addFiles(files);
  };

  const canSubmit = isEditing
    ? Boolean(value.trim()) && !sending
    : (Boolean(value.trim()) || pending.length > 0) && !sending;

  return (
    <div className="px-4 pb-4 pt-2">
      {(sendError || localError) && (
        <div
          role="alert"
          aria-live="polite"
          data-testid="im-composer-send-error"
          className={cn(
            "mb-2 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm",
            noticeChipNeutral,
          )}
        >
          <AlertTriangle size={15} className="shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">{sendError ?? localError}</span>
          <IconButton
            onClick={() => {
              clearSendError();
              setLocalError(null);
            }}
            aria-label="关闭"
            className="text-muted-foreground hover:bg-transparent hover:text-foreground"
          >
            <X size={14} />
          </IconButton>
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`relative rounded-xl border bg-card shadow-sm transition-colors ${
          dragging ? "border-primary bg-primary/5" : "border-border"
        }`}
      >
        {mentionOpen && (
          <ChatMentionMenu
            items={mentionItems}
            activeIndex={mentionActive}
            query={mentionQuery}
            onHover={setMentionActive}
            onSelect={insertMention}
          />
        )}

        {editTarget && (
          <div className="flex items-start gap-2 border-b border-border px-3 py-2">
            <div className="min-w-0 flex-1 border-l-2 border-primary pl-2">
              <span className="block truncate text-xs font-medium text-foreground">
                编辑消息
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                保存后将替换原正文
              </span>
            </div>
            <IconButton
              onClick={() => {
                setValue("");
                onClearEdit?.();
              }}
              aria-label="取消编辑"
              className="shrink-0 text-muted-foreground"
            >
              <X size={14} />
            </IconButton>
          </div>
        )}

        {replyTarget && !editTarget && (
          <div className="flex items-start gap-2 border-b border-border px-3 py-2">
            <div className="min-w-0 flex-1 border-l-2 border-primary pl-2">
              <span className="block truncate text-xs font-medium text-foreground">
                回复 {replyTarget.snapshot.sender_display_name}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {replyTarget.snapshot.body_preview}
              </span>
            </div>
            <IconButton
              onClick={() => onClearReply?.()}
              aria-label="取消回复"
              className="shrink-0 text-muted-foreground"
            >
              <X size={14} />
            </IconButton>
          </div>
        )}

        {pending.length > 0 && !isEditing && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {pending.map((p) => (
              <div
                key={p.id}
                className="group/att relative flex items-center gap-2 rounded-lg border border-border bg-background py-1.5 pl-1.5 pr-2"
              >
                {p.previewUrl ? (
                  <img
                    src={p.previewUrl}
                    alt={p.file.name}
                    className="size-9 rounded-lg object-cover"
                  />
                ) : (
                  <span className="flex size-9 items-center justify-center rounded-lg bg-muted">
                    <FileText size={16} className="text-muted-foreground" />
                  </span>
                )}
                <span className="max-w-[140px] truncate text-xs text-foreground">
                  {p.file.name}
                </span>
                <IconButton
                  onClick={() => removePending(p.id)}
                  aria-label="移除附件"
                  className="size-4 rounded-full bg-muted text-muted-foreground hover:bg-destructive hover:text-destructive-foreground"
                >
                  <X size={11} />
                </IconButton>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2 px-3 py-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              addFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          {!isEditing && (
            <IconButton
              size="md"
              onClick={() => fileInputRef.current?.click()}
              disabled={sending}
              aria-label="添加附件"
            >
              <Paperclip size={16} />
            </IconButton>
          )}
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => {
              const next = e.target.value;
              setValue(next);
              if (!isEditing) {
                syncMentionDraft(next, e.target.selectionStart ?? next.length);
              }
            }}
            onKeyDown={handleKeyDown}
            onClick={(e) => {
              if (isEditing) return;
              const el = e.currentTarget;
              syncMentionDraft(el.value, el.selectionStart ?? el.value.length);
            }}
            onSelect={(e) => {
              if (isEditing) return;
              const el = e.currentTarget;
              syncMentionDraft(el.value, el.selectionStart ?? el.value.length);
            }}
            onPaste={handlePaste}
            placeholder={
              isEditing ? "编辑消息…" : replyTarget ? "输入回复…" : "输入消息…"
            }
            className="max-h-40 w-full resize-none bg-transparent py-1 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
            rows={1}
          />
          <IconButton
            size="md"
            tone="primary"
            onClick={handleSend}
            disabled={!canSubmit}
            aria-label={isEditing ? "保存" : "发送"}
          >
            {sending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Send size={14} />
            )}
          </IconButton>
        </div>
      </div>
    </div>
  );
}
