// IM bubble/composer display helpers (mobile-local; mirrors desktop chatDisplay
// semantics without sharing implementation — cross-platform-frontend.mdc).
import {
  type ChatMessageDetail,
  type ChatSummary,
  isImageAttachment,
} from "@/api/messaging";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

export type ChatMention =
  | Schemas["MessageMentionUser"]
  | Schemas["MessageMentionEveryone"];

export type MessageReplyTo = Schemas["ReplyToSnapshot"];

/** Visible body token for `@所有人` (display only; truth is `kind: "everyone"`). */
export const EVERYONE_MENTION_LABEL = "所有人";

type ChatParticipant = Schemas["ChatParticipant"];

/** Visible governance mark (platform / 群主 / 管理员). */
export type GovernanceBadgeKind = "platform" | "owner" | "admin";

export interface MemberGovernanceBadge {
  kind: GovernanceBadgeKind;
  label: string;
  shortLabel: string;
}

/**
 * One roster/bubble badge. Platform admin wins over group role;
 * owner → 群主; group admin → 管理员. Mirrors desktop chatDisplay.
 */
export function memberGovernanceBadge(
  member: Pick<ChatParticipant, "is_admin" | "group_role">,
): MemberGovernanceBadge | null {
  if (member.is_admin) {
    return { kind: "platform", label: "平台管理员", shortLabel: "平台" };
  }
  if (member.group_role === "owner") {
    return { kind: "owner", label: "群主", shortLabel: "群主" };
  }
  if (member.group_role === "admin") {
    return { kind: "admin", label: "管理员", shortLabel: "管理员" };
  }
  return null;
}

/**
 * Bubble circle: own auth URL; DM peer; group roster member; official session
 * icon. DM / group never fall back to `chat.avatar_url`.
 */
export function bubbleAvatarUrl(opts: {
  mine: boolean;
  chatType: ChatSummary["type"] | null | undefined;
  myAvatarUrl: string | null | undefined;
  peerAvatarUrl: string | null | undefined;
  memberAvatarUrl: string | null | undefined;
  chatAvatarUrl: string | null | undefined;
}): string | null {
  if (opts.mine) return opts.myAvatarUrl ?? null;
  if (opts.chatType === "group") return opts.memberAvatarUrl ?? null;
  if (opts.chatType === "dm") return opts.peerAvatarUrl ?? null;
  return opts.chatAvatarUrl ?? null;
}

/** Max chars for a reply quote preview; matches server `_REPLY_PREVIEW_MAX`. */
export const REPLY_BODY_PREVIEW_MAX = 100;

/** Truncate a reply body preview with an ellipsis when over the soft cap. */
export function truncateReplyPreview(
  text: string,
  max = REPLY_BODY_PREVIEW_MAX,
): string {
  const compact = text.trim().replace(/\s+/g, " ");
  if (!compact) return "";
  if (compact.length <= max) return compact;
  return `${compact.slice(0, max)}…`;
}

/** Body/attachment label used in reply quotes (never empty for a sendable msg). */
export function replyBodyPreview(message: ChatMessageDetail): string {
  const text = truncateReplyPreview(message.content ?? "");
  if (text) return text;
  const attachments = message.attachments ?? [];
  if (attachments.length > 0) {
    if (attachments.every((a) => isImageAttachment(a.name))) return "[图片]";
    return "[文件]";
  }
  switch (message.content_type) {
    case "image":
      return "[图片]";
    case "file":
      return "[文件]";
    case "system_card":
      return "[通知]";
    default:
      return "";
  }
}

/**
 * Build a local reply snapshot from the message being replied to (composer bar
 * until the server returns `reply_to` on the sent message).
 */
export function buildReplySnapshot(
  message: ChatMessageDetail,
  senderDisplayName: string,
): MessageReplyTo {
  return {
    sender_user_id: message.sender_user_id,
    sender_display_name: senderDisplayName.trim() || "成员",
    body_preview: replyBodyPreview(message),
  };
}

/** Visible `@…` token for a structured mention (body display; not the truth source). */
export function mentionAtToken(
  mention: ChatMention,
  resolveUserName: (userId: string) => string | undefined,
): string {
  if (mention.kind === "everyone") return `@${EVERYONE_MENTION_LABEL}`;
  const name = resolveUserName(mention.user_id)?.trim() || "成员";
  return `@${name}`;
}

/** Whether this message @-mentions `userId` or everyone. */
export function messageMentionsUser(
  message: Pick<ChatMessageDetail, "mentions">,
  userId: string | null | undefined,
): boolean {
  const mentions = message.mentions;
  if (!mentions || mentions.length === 0) return false;
  for (const m of mentions) {
    if (m.kind === "everyone") return true;
    if (userId && m.kind === "user" && m.user_id === userId) return true;
  }
  return false;
}

export type MentionContentSegment =
  | { type: "text"; text: string }
  | { type: "mention"; text: string; self: boolean };

/**
 * Light highlighter: wrap body `@token`s that match structured `mentions`.
 * Longest token first to avoid partial overlaps; no heavy parser.
 */
export function splitContentByMentions(
  content: string,
  mentions: readonly ChatMention[] | null | undefined,
  resolveUserName: (userId: string) => string | undefined,
  myUserId?: string | null,
): MentionContentSegment[] {
  if (!content) return [];
  if (!mentions || mentions.length === 0) {
    return [{ type: "text", text: content }];
  }

  const tokens: { token: string; self: boolean }[] = [];
  const seen = new Set<string>();
  for (const m of mentions) {
    const token = mentionAtToken(m, resolveUserName);
    if (seen.has(token)) continue;
    seen.add(token);
    const self =
      m.kind === "everyone" ||
      (m.kind === "user" && !!myUserId && m.user_id === myUserId);
    tokens.push({ token, self });
  }
  tokens.sort((a, b) => b.token.length - a.token.length);
  if (tokens.length === 0) return [{ type: "text", text: content }];

  const segments: MentionContentSegment[] = [];
  let i = 0;
  while (i < content.length) {
    let hit: { token: string; self: boolean } | null = null;
    let hitAt = -1;
    for (const t of tokens) {
      const at = content.indexOf(t.token, i);
      if (at === -1) continue;
      if (hitAt === -1 || at < hitAt) {
        hitAt = at;
        hit = t;
      }
    }
    if (!hit || hitAt === -1) {
      segments.push({ type: "text", text: content.slice(i) });
      break;
    }
    if (hitAt > i) {
      segments.push({ type: "text", text: content.slice(i, hitAt) });
    }
    segments.push({ type: "mention", text: hit.token, self: hit.self });
    i = hitAt + hit.token.length;
  }
  return segments;
}
