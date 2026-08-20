import { Button, IconButton } from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { formatMessageTimeOfDay } from "@/lib/format";
import type { ImBubbleLayout } from "@/lib/imMessageLayout";
import { notifyActionError } from "@/lib/toast";
import {
  type ChatMessageDetail,
  type MessageReplyTo,
  downloadChatAttachment,
  isImageAttachment,
} from "@/services/messaging";
import type { ChatType } from "@/services/messaging";
import { Download, FileText, Folder, Pencil, Reply, Undo2 } from "lucide-react";
import type { ReactNode } from "react";
import { ChatImageGallery } from "./ChatImageGallery";
import { GovernanceBadge } from "./GovernanceBadge";
import { PresenceAvatar } from "./PresenceAvatar";
import { ProductNoticeCard } from "./ProductNoticeCard";
import {
  type MemberGovernanceBadge,
  avatarInitial,
  canOfferEdit,
  canOfferRecall,
  messageMentionsUser,
  splitContentByMentions,
} from "./chatDisplay";
import { asProductNoticePayload } from "./productNotice";

interface Props {
  message: ChatMessageDetail;
  /** Sent by the viewing user → right-aligned. */
  mine: boolean;
  /** Sender's display name, shown above the bubble in group threads (others only). */
  senderName?: string;
  /** Group governance mark next to the sender name (others only). */
  senderGovernance?: MemberGovernanceBadge | null;
  /** Fallback label for the avatar initial. */
  avatarName?: string;
  /** Profile image when available. */
  senderAvatarUrl?: string | null;
  layout: ImBubbleLayout;
  /** Brief flash after scroll-to-reply lands on this message. */
  highlighted?: boolean;
  /** Viewing user id — drives self-mention highlight in body text. */
  myUserId?: string | null;
  /** Platform admin — may recall others' group / system_card messages. */
  isAdmin?: boolean;
  /** Group 管理员 (owner/admin) — may recall others' group messages (not system_card). */
  isGroupModerator?: boolean;
  /** Active chat type (gates admin recall menu). */
  chatType?: ChatType | null;
  /** Resolve a mentioned user's display name for body `@token` matching. */
  resolveMentionName?: (userId: string) => string | undefined;
  /** Start a reply to this message (hover button / context menu). */
  onReply?: (message: ChatMessageDetail) => void;
  /** Soft-recall this message. */
  onRecall?: (message: ChatMessageDetail) => void;
  /** Start editing this message in the composer. */
  onEdit?: (message: ChatMessageDetail) => void;
  /** Click the quote block → scroll to the original message. */
  onScrollToReply?: (messageId: string) => void;
  /** True when the quoted target was later recalled (still show snapshot). */
  replyTargetRecalled?: boolean;
  /** Group bubble avatar → 资料卡 (消息IM.md §9.4). */
  onAvatarClick?: (userId: string) => void;
}

function textBubbleRadius(
  mine: boolean,
  position: ImBubbleLayout["clusterPosition"],
) {
  if (position === "single") return "rounded-xl";
  if (mine) {
    if (position === "first") return "rounded-xl rounded-br-sm";
    if (position === "middle") return "rounded-lg rounded-r-xl";
    return "rounded-xl rounded-tr-sm";
  }
  if (position === "first") return "rounded-xl rounded-bl-sm";
  if (position === "middle") return "rounded-lg rounded-l-xl";
  return "rounded-xl rounded-tl-sm";
}

/** A human-readable byte size for a file chip (e.g. "1.2 MB"). */
function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Inline reply quote inside a bubble (not the AI-chat @attachment chip).
 * Isolate from the bubble's pre-wrap / overflow-wrap so 2-line clamp can fire.
 */
function ReplyQuote({
  reply,
  mine,
  targetRecalled,
  onClick,
}: {
  reply: MessageReplyTo;
  mine: boolean;
  targetRecalled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`mb-1.5 w-full min-w-0 max-w-full overflow-hidden whitespace-normal rounded-lg border-l-2 px-2 py-1 text-left [overflow-wrap:break-word] transition-colors ${
        mine
          ? "border-primary-foreground/40 bg-primary-foreground/10 text-primary-foreground/80 hover:bg-primary-foreground/15"
          : "border-primary/50 bg-muted/60 text-muted-foreground hover:bg-muted"
      } ${onClick ? "cursor-pointer" : "cursor-default"}`}
    >
      <span className="block truncate text-xs font-medium">
        {reply.sender_display_name}
        {targetRecalled ? " · 原消息已撤回" : ""}
      </span>
      <span className="block line-clamp-2 min-w-0 break-words text-xs opacity-80">
        {reply.body_preview}
      </span>
    </button>
  );
}

function recallPlaceholderLabel(
  message: ChatMessageDetail,
  mine: boolean,
  myUserId: string | null | undefined,
  senderName: string | undefined,
): string {
  const by = message.recalled_by_user_id;
  if (by && myUserId && by === myUserId) return "你撤回了一条消息";
  if (by && message.sender_user_id && by !== message.sender_user_id) {
    return "管理员撤回了一条消息";
  }
  if (mine) return "你撤回了一条消息";
  const name = senderName?.trim();
  return name ? `${name}撤回了一条消息` : "有人撤回了一条消息";
}

/** Render body text with light @ mention highlights (structured mentions as source). */
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
  myUserId?: string | null;
  resolveMentionName?: (userId: string) => string | undefined;
}): ReactNode {
  const segments = splitContentByMentions(
    content,
    mentions,
    resolveMentionName ?? (() => undefined),
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
        const selfTone = seg.self
          ? mine
            ? "bg-primary-foreground/25 text-primary-foreground"
            : "bg-primary/15 text-primary"
          : mine
            ? "bg-primary-foreground/15 text-primary-foreground"
            : "bg-muted text-foreground";
        return (
          <span
            key={key}
            className={`rounded-lg px-0.5 font-medium ${selfTone}`}
          >
            {seg.text}
          </span>
        );
      })}
    </>
  );
}

/**
 * One IM message bubble. Human chat is rendered as plain wrapped text (not
 * Markdown): a stray `#`/`*` in a person's message shouldn't become a heading,
 * and own-bubble theming (primary background) would fight Markdown's fixed
 * foreground color.
 *
 * Width follows mainstream IM (WeChat / WhatsApp): shrink-wrap short messages,
 * cap at `min(75%, 24rem)` so wide threads don't paint full-row blue bars.
 * Long tokens use `overflow-wrap: anywhere` so URLs don't blow the cap.
 *
 * 富消息: image attachments render via {@link ChatImageGallery} (thumb grid +
 * lightbox); other files render as download chips. `system_card` renders as a
 * centered pill, or a product-notice card when `payload.kind === "product_notice"`.
 */
export function ChatBubble({
  message,
  mine,
  senderName,
  senderGovernance = null,
  avatarName,
  senderAvatarUrl,
  layout,
  highlighted = false,
  myUserId,
  isAdmin = false,
  isGroupModerator = false,
  chatType = null,
  resolveMentionName,
  onReply,
  onRecall,
  onEdit,
  onScrollToReply,
  replyTargetRecalled = false,
  onAvatarClick,
}: Props) {
  const time = formatMessageTimeOfDay(message.created_at);

  if (message.recalled_at) {
    return (
      <div data-message-id={message.id} className="flex justify-center py-1">
        <span className="rounded-lg bg-muted px-2.5 py-1 text-xs text-muted-foreground">
          {recallPlaceholderLabel(message, mine, myUserId, senderName)}
        </span>
      </div>
    );
  }

  if (message.content_type === "system_card") {
    const notice = asProductNoticePayload(message.payload);
    if (notice) {
      const offerRecall = canOfferRecall(message, {
        mine,
        isAdmin,
        isGroupModerator,
        chatType,
      });
      const card = <ProductNoticeCard message={message} payload={notice} />;
      if (!offerRecall || !onRecall) return card;
      return (
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div data-message-id={message.id}>{card}</div>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuItem onSelect={() => onRecall(message)}>
              <Undo2 size={14} />
              撤回
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
      );
    }
    const offerRecall = canOfferRecall(message, {
      mine,
      isAdmin,
      isGroupModerator,
      chatType,
    });
    const pill = (
      <div data-message-id={message.id} className="flex justify-center py-1">
        <span className="rounded-lg bg-muted px-2.5 py-1 text-xs text-muted-foreground">
          {message.content || "[通知]"}
        </span>
      </div>
    );
    if (!offerRecall || !onRecall) return pill;
    return (
      <ContextMenu>
        <ContextMenuTrigger asChild>{pill}</ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem onSelect={() => onRecall(message)}>
            <Undo2 size={14} />
            撤回
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    );
  }

  const attachments = message.attachments ?? [];
  const images = attachments.filter(
    (a) => a.kind !== "dir" && a.workspace_path && isImageAttachment(a.name),
  );
  const files = attachments.filter((a) => !images.includes(a));
  const hasText = Boolean(message.content);
  const reply = message.reply_to ?? null;
  const canReply = Boolean(onReply);
  const offerRecall = canOfferRecall(message, {
    mine,
    isAdmin,
    isGroupModerator,
    chatType,
  });
  const canRecall = offerRecall && Boolean(onRecall);
  const offerEdit = canOfferEdit(message, { mine, chatType });
  const canEdit = offerEdit && Boolean(onEdit);
  const hasMenu = canReply || canRecall || canEdit;
  const replyTargetId = message.reply_to_message_id ?? null;
  const mentionedMe = !mine && messageMentionsUser(message, myUserId ?? null);
  const edited = Boolean(message.edited_at);

  const avatarLabel = avatarName ?? senderName ?? "?";

  const bubbleBody = (
    <div
      className={`flex min-w-0 flex-col ${mine ? "items-end" : "items-start"}`}
    >
      {!mine && layout.showSenderName && senderName && (
        <span className="mb-0.5 flex min-w-0 flex-wrap items-center gap-1 px-1 text-xs text-muted-foreground">
          <span className="truncate">{senderName}</span>
          {senderGovernance && (
            <GovernanceBadge badge={senderGovernance} compact />
          )}
        </span>
      )}

      <div
        className={`flex min-w-0 max-w-full flex-col gap-1.5 ${
          mine ? "items-end" : "items-start"
        }`}
      >
        {(hasText || reply) && (
          <div
            className={`w-fit max-w-full min-w-0 whitespace-pre-wrap break-words [overflow-wrap:anywhere] px-3 py-2 text-sm ${textBubbleRadius(
              mine,
              layout.clusterPosition,
            )} ${
              mine
                ? "bg-primary text-primary-foreground"
                : mentionedMe
                  ? "border border-primary/40 bg-primary/5 text-foreground"
                  : "border border-border bg-card text-foreground"
            }`}
          >
            {reply && (
              <ReplyQuote
                reply={reply}
                mine={mine}
                targetRecalled={replyTargetRecalled}
                onClick={
                  replyTargetId && onScrollToReply
                    ? () => onScrollToReply(replyTargetId)
                    : undefined
                }
              />
            )}
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
          <ChatImageGallery chatId={message.chat_id} images={images} />
        )}

        {files.map((a) => {
          const downloadable = a.kind !== "dir" && Boolean(a.workspace_path);
          return (
            <Button
              key={a.workspace_path ?? a.path}
              variant="ghost"
              disabled={!downloadable}
              onClick={() =>
                downloadable &&
                a.workspace_path &&
                void downloadChatAttachment(
                  message.chat_id,
                  a.workspace_path,
                  a.name,
                ).catch((e) => notifyActionError("下载失败", e))
              }
              className={`h-auto w-full max-w-[260px] gap-2 rounded-xl border border-border bg-card px-3 py-2 font-normal ${
                mine ? "justify-end text-right" : "justify-start text-left"
              } ${downloadable ? "hover:bg-accent" : "opacity-70"}`}
            >
              <span className="flex w-full items-center gap-2">
                {a.kind === "dir" ? (
                  <Folder
                    size={16}
                    className="shrink-0 text-muted-foreground"
                  />
                ) : (
                  <FileText
                    size={16}
                    className="shrink-0 text-muted-foreground"
                  />
                )}
                <span className="min-w-0 flex-1">
                  <SimpleTooltip label={a.name}>
                    <span className="block truncate text-sm text-foreground">
                      {a.name}
                      {a.kind === "dir" ? "/" : ""}
                    </span>
                  </SimpleTooltip>
                  {a.size_bytes != null && (
                    <span className="block text-xs text-muted-foreground">
                      {formatBytes(a.size_bytes)}
                    </span>
                  )}
                </span>
                {downloadable && (
                  <Download
                    size={14}
                    className="shrink-0 text-muted-foreground"
                  />
                )}
              </span>
            </Button>
          );
        })}
      </div>
    </div>
  );

  const content = (
    <div
      data-message-id={message.id}
      className={`group flex w-fit max-w-[min(75%,24rem)] flex-col rounded-xl transition-colors ${
        mine ? "ml-auto items-end" : "items-start"
      } ${layout.tightTop ? "-mt-1" : ""} ${
        highlighted ? "bg-primary/10 ring-1 ring-primary/30" : ""
      }`}
    >
      <div
        className={`flex items-start gap-1.5 ${mine ? "flex-row-reverse" : ""}`}
      >
        <div
          className={`mt-0.5 shrink-0 ${layout.showAvatar ? "" : "invisible"}`}
          aria-hidden={!layout.showAvatar}
        >
          <PresenceAvatar
            label={avatarInitial(avatarLabel)}
            url={senderAvatarUrl}
            sizeClass="size-8"
            textClass="text-xs"
            onClick={
              layout.showAvatar &&
              !mine &&
              message.sender_user_id &&
              onAvatarClick
                ? () => onAvatarClick(message.sender_user_id as string)
                : undefined
            }
            ariaLabel={`查看 ${avatarLabel} 的资料`}
          />
        </div>

        {bubbleBody}

        {canReply && (
          <SimpleTooltip label="回复">
            <IconButton
              size="sm"
              onClick={() => onReply?.(message)}
              aria-label="回复"
              className="mt-0.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
            >
              <Reply size={14} />
            </IconButton>
          </SimpleTooltip>
        )}
      </div>

      {(time || edited) && (
        <span
          className={`flex items-center gap-1.5 px-1 text-xs text-muted-foreground ${
            mine ? "mr-10 justify-end" : "ml-10"
          }`}
        >
          {edited && <span>已编辑</span>}
          {time && (
            <SimpleTooltip
              label={new Date(message.created_at).toLocaleString()}
            >
              <span className="cursor-default opacity-0 transition-opacity group-hover:opacity-100">
                {time}
              </span>
            </SimpleTooltip>
          )}
        </span>
      )}
    </div>
  );

  if (!hasMenu) return content;

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{content}</ContextMenuTrigger>
      <ContextMenuContent>
        {canReply && (
          <ContextMenuItem onSelect={() => onReply?.(message)}>
            <Reply size={14} />
            回复
          </ContextMenuItem>
        )}
        {canEdit && (
          <ContextMenuItem onSelect={() => onEdit?.(message)}>
            <Pencil size={14} />
            编辑
          </ContextMenuItem>
        )}
        {canRecall && (
          <ContextMenuItem onSelect={() => onRecall?.(message)}>
            <Undo2 size={14} />
            撤回
          </ContextMenuItem>
        )}
      </ContextMenuContent>
    </ContextMenu>
  );
}
