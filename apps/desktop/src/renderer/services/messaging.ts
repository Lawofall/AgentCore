import { ApiError, BASE_URL, NetworkError, api } from "@/services/api";
import { authedFetch, encodePath, saveBlob } from "@/services/workspaceHttp";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/**
 * 消息 page (找人 IM) REST client — mirrors `apps/server/.../api/routes/messages.py`
 * and its Pydantic schemas (消息IM.md §三).
 *
 * The 消息 page is human↔human, a domain separate from the 对话 page's AI
 * conversations, so this is its own service with its own types. The REST types
 * below are GENERATED from the backend OpenAPI spec (`types/api.generated.ts`,
 * via root `pnpm gen:types`) and aliased here, so they track `api/schemas.py` with zero
 * hand-written drift. Wire fields stay snake_case verbatim. Fields the backend
 * gives a default (e.g. `unread`, `avatar_url`, `attachments`) are optional in
 * the generated type; read sites coalesce where a concrete value is needed.
 */

/** Chat kind (generated from backend `ChatSummary.type`). */
export type ChatType = Schemas["ChatSummary"]["type"];
/** This user's membership state on a chat (generated from `ChatSummary.state`). */
export type ChatMemberState = Schemas["ChatSummary"]["state"];
/** Message author kind (generated from `ChatMessageDetail.sender_type`). */
export type ChatSenderType = Schemas["ChatMessageDetail"]["sender_type"];
/** Message body kind (generated from `ChatMessageDetail.content_type`). */
export type MessageContentType = Schemas["ChatMessageDetail"]["content_type"];
/** Who may DM this user (generated from `DirectorySettings.who_can_dm`). */
export type WhoCanDm = Schemas["DirectorySettings"]["who_can_dm"];

/** Who may send this user a friend request. */
export type WhoCanFriend = Schemas["DirectorySettings"]["who_can_friend"];

/** Viewer↔target relationship on a profile card. */
export type ProfileRelation = Schemas["UserProfile"]["relation"];

/** A human shown on a chat (the peer of a dm; a member of a group). */
export type ChatParticipant = Schemas["ChatParticipant"];

/** Persisted attachment display metadata (generated from `StoredAttachment`). */
export type StoredAttachment = Schemas["StoredAttachment"];

/** One row in the IM chat list (消息页左栏), plus this user's per-chat state. */
export type ChatSummary = Schemas["ChatSummary"];

/** Frozen quote snapshot on a replied message (generated from `ReplyToSnapshot`). */
export type MessageReplyTo = Schemas["ReplyToSnapshot"];

/** Structured @ mention on an IM message (generated from OpenAPI). */
export type ChatMention =
  | Schemas["MessageMentionUser"]
  | Schemas["MessageMentionEveryone"];

/** One message in a chat thread (generated from `ChatMessageDetail`). */
export type ChatMessageDetail = Schemas["ChatMessageDetail"];

/** A discoverable user surfaced by people-search (任意搜人, exact match). */
export type UserSearchResult = Schemas["UserSearchResult"];

/** A user this account has blocked. */
export type BlockedUser = Schemas["BlockedUser"];

/** Discoverability + who-can-friend + who-can-DM privacy. */
export type DirectorySettings = Schemas["DirectorySettings"];

/** Profile card payload (`GET /v1/messages/users/{id}/profile`). */
export type UserProfile = Schemas["UserProfile"];

/** One row in the contacts (friends) list. */
export type FriendSummary = Schemas["FriendSummary"];

/** A friend-request row (incoming or outgoing); peer = the other party. */
export type FriendRequest = Schemas["FriendRequestDetail"];

/** Friend-request inbox (`GET /v1/messages/friends/requests`). */
export type FriendRequestsBox = Schemas["FriendRequestListResponse"];

/** Firehose `friend_request` action (消息IM.md §9.3; not in OpenAPI REST). */
export type FriendRequestAction =
  | "created"
  | "accepted"
  | "rejected"
  | "cancelled";

type ChatListResponse = Schemas["ChatListResponse"];
type ChatMembersResponse = Schemas["ChatMembersResponse"];
type UserSearchResponse = Schemas["UserSearchResponse"];
type ChatMessageListResponse = Schemas["ChatMessageListResponse"];
type BlockListResponse = Schemas["BlockListResponse"];

/** Normalize who-can-DM for UI selection (`friends` / `anyone` only). */
export function normalizeWhoCanDm(
  value: WhoCanDm | string | null | undefined,
): "anyone" | "friends" {
  if (value === "friends") return "friends";
  return "anyone";
}
/** A page of a chat's messages (oldest first), paging echoed back. */
export interface MessagePage {
  messages: ChatMessageDetail[];
  total: number;
  page: number;
  pageSize: number;
}

// --- People search (任意搜人) ---

/** Exact-match people-search for starting a chat (visibility filtered server-side). */
export async function searchUsers(
  query: string,
  limit = 20,
): Promise<UserSearchResult[]> {
  const res = await api.get<UserSearchResponse>(
    `/v1/messages/users/search?q=${encodeURIComponent(query)}&limit=${limit}`,
  );
  return res.data;
}

// --- Chats ---

/** This user's chat list (recent first), with unread counts and dm peers. */
export async function listChats(): Promise<ChatSummary[]> {
  const res = await api.get<ChatListResponse>("/v1/messages/chats");
  return res.data;
}

/** Open (or reuse) a 1:1 chat with another user (by their user id). */
export async function startDm(userId: string): Promise<ChatSummary> {
  return api.post<ChatSummary>("/v1/messages/chats/dm", { user_id: userId });
}

/** A chat's members (group roster: resolves sender names + member panel). */
export async function listMembers(chatId: string): Promise<ChatParticipant[]> {
  const res = await api.get<ChatMembersResponse>(
    `/v1/messages/chats/${chatId}/members`,
  );
  return res.data;
}

/** Patch this user's per-chat flags (mute / pin); returns the updated row. */
export async function updateMembership(
  chatId: string,
  patch: { muted?: boolean; pinned?: boolean },
): Promise<ChatSummary> {
  return api.patch<ChatSummary>(
    `/v1/messages/chats/${chatId}/membership`,
    patch,
  );
}

/** Leave a group/official chat (removes this user's membership). */
export async function leaveChat(chatId: string): Promise<void> {
  await api.post(`/v1/messages/chats/${chatId}/leave`, {});
}

// --- Moderation (Stage 3 审核治理: 平台 admin only; gated server-side) ---

/** Remove a member from a group (admin 踢人). Posts a system notice server-side. */
export async function kickMember(
  chatId: string,
  userId: string,
): Promise<void> {
  await api.delete(`/v1/messages/chats/${chatId}/members/${userId}`);
}

/** Mute / unmute a member (admin 禁言): a muted member can read but not send. */
export async function muteMember(
  chatId: string,
  userId: string,
  muted: boolean,
): Promise<void> {
  await api.post(`/v1/messages/chats/${chatId}/members/${userId}/mute`, {
    muted,
  });
}

/** Post an admin announcement as a centered system_card (官方公告), fanned out to
 * every member. Returns the stored message (the firehose also delivers it). */
export async function announce(
  chatId: string,
  content: string,
): Promise<ChatMessageDetail> {
  return api.post<ChatMessageDetail>(`/v1/messages/chats/${chatId}/announce`, {
    content,
  });
}

// --- Messages ---

/** A page of a chat's messages (oldest first). */
export async function listMessages(
  chatId: string,
  page = 1,
  pageSize = 50,
): Promise<MessagePage> {
  const res = await api.get<ChatMessageListResponse>(
    `/v1/messages/chats/${chatId}/messages?page=${page}&page_size=${pageSize}`,
  );
  return {
    messages: res.data,
    total: res.total,
    page: res.page,
    pageSize: res.page_size,
  };
}

/** Body kind a sender may set (excludes `system_card`, which is server-minted). */
export type SendContentType = Schemas["SendChatMessageRequest"]["content_type"];

export interface SendMessageInput {
  /** Optional when `attachments` is non-empty (an image/file-only message). */
  content?: string;
  /** Render hint: `image` for an inline gallery, `file` for download chips. */
  contentType?: SendContentType;
  /** Pre-uploaded attachments (via {@link uploadChatFile}) to reference. */
  attachments?: StoredAttachment[];
  /** Client-minted id for retry-safe idempotent send (server dedups). */
  clientMsgId?: string;
  replyToMessageId?: string;
  /** Structured @ mentions (S2); body still carries visible `@显示名` / `@所有人`. */
  mentions?: ChatMention[];
}

/** Send a message into a chat the user belongs to (text and/or attachments). */
export async function sendMessage(
  chatId: string,
  input: SendMessageInput,
): Promise<ChatMessageDetail> {
  return api.post<ChatMessageDetail>(`/v1/messages/chats/${chatId}/messages`, {
    content: input.content,
    content_type: input.contentType ?? "text",
    attachments: input.attachments ?? [],
    client_msg_id: input.clientMsgId,
    reply_to_message_id: input.replyToMessageId,
    ...(input.mentions && input.mentions.length > 0
      ? { mentions: input.mentions }
      : {}),
  });
}

/** Soft-recall a message (S3). Returns the updated row (body cleared). */
export async function recallMessage(
  chatId: string,
  messageId: string,
): Promise<ChatMessageDetail> {
  return api.post<ChatMessageDetail>(
    `/v1/messages/chats/${chatId}/messages/${messageId}/recall`,
    {},
  );
}

/** Edit a plain-text message (S4). Returns the updated row with ``edited_at``. */
export async function editMessage(
  chatId: string,
  messageId: string,
  content: string,
): Promise<ChatMessageDetail> {
  return api.patch<ChatMessageDetail>(
    `/v1/messages/chats/${chatId}/messages/${messageId}`,
    { content },
  );
}

// --- Attachments (Stage 4 富消息: 图/文件, 复用工作区存储) ---
// Two-step like the workspace file API: PUT the raw bytes into the chat's shared
// space, then reference the returned path in a sendMessage attachment. These
// bypass the JSON `api` helper (raw bytes / blobs) but reuse its cookie-auth +
// refresh-once policy via `authedFetch`.

type ChatFileUploadResponse = Schemas["ChatFileUploadResponse"];

const chatFilesUrl = (chatId: string, path: string): string =>
  `${BASE_URL}/v1/messages/chats/${chatId}/files/${encodePath(path)}`;

/**
 * Upload an attachment's raw bytes into a chat's space; returns its stored path,
 * size, and (for an image) a generated WebP `thumb_path` for cheap inline
 * previews. The caller copies these onto the message's {@link StoredAttachment}.
 */
export async function uploadChatFile(
  chatId: string,
  path: string,
  body: Blob,
): Promise<ChatFileUploadResponse> {
  const res = await authedFetch(chatFilesUrl(chatId, path), {
    method: "PUT",
    body,
  });
  return res.json() as Promise<ChatFileUploadResponse>;
}

/** Fetch an attachment as a Blob (for inline image rendering via object URL). */
export async function fetchChatAttachmentBlob(
  chatId: string,
  workspacePath: string,
): Promise<Blob> {
  const res = await authedFetch(chatFilesUrl(chatId, workspacePath));
  return res.blob();
}

/** Download an attachment and save it to disk via the browser. */
export async function downloadChatAttachment(
  chatId: string,
  workspacePath: string,
  filename: string,
): Promise<void> {
  const res = await authedFetch(chatFilesUrl(chatId, workspacePath));
  await saveBlob(await res.blob(), filename);
}

// Raster formats safe to render inline via <img src=blob>. SVG is intentionally
// excluded (shown as a file chip) — it is a document, not just a bitmap. Used by
// both the sender (to derive a message's content_type) and the bubble (to decide
// per-attachment whether to inline an image or show a download chip).
const IMAGE_EXTENSIONS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "bmp",
  "avif",
]);

/** Whether a filename looks like an inline-renderable image (by extension). */
export function isImageAttachment(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot === -1) return false;
  return IMAGE_EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
}

/**
 * Pasted / attached image files are thumbs, not filename chips.
 * Directories, conversation cites, and prompt documents stay chips.
 */
export function isVisualImageAttachment(att: {
  name: string;
  kind?: string | null;
}): boolean {
  if (att.kind && att.kind !== "file") return false;
  return isImageAttachment(att.name);
}

/** Advance this user's read cursor (drives unread counts). */
export async function markRead(
  chatId: string,
  lastReadMessageId: string,
): Promise<void> {
  await api.post(`/v1/messages/chats/${chatId}/read`, {
    last_read_message_id: lastReadMessageId,
  });
}

// --- Friends / profile (消息IM.md §9.3) ---

type FriendListResponse = Schemas["FriendListResponse"];
type UpdateDirectorySettingsRequest = Schemas["UpdateDirectorySettingsRequest"];

/** Profile card for a user (404 if not visible — do not leak existence). */
export async function getUserProfile(userId: string): Promise<UserProfile> {
  return api.get<UserProfile>(`/v1/messages/users/${userId}/profile`);
}

/** Accepted friends = 通讯录. */
export async function listFriends(): Promise<FriendSummary[]> {
  const res = await api.get<FriendListResponse>("/v1/messages/friends");
  return res.data;
}

/** Pending friend-request inbox (incoming + outgoing). */
export async function listFriendRequests(): Promise<FriendRequestsBox> {
  const res = await api.get<FriendRequestsBox>("/v1/messages/friends/requests");
  return { incoming: res.incoming ?? [], outgoing: res.outgoing ?? [] };
}

/** Send a friend request (optional verification message). */
export async function sendFriendRequest(
  userId: string,
  message?: string,
): Promise<FriendRequest> {
  return api.post<FriendRequest>("/v1/messages/friends/requests", {
    user_id: userId,
    ...(message?.trim() ? { message: message.trim() } : {}),
  });
}

export async function acceptFriendRequest(
  requestId: string,
): Promise<FriendRequest> {
  return api.post<FriendRequest>(
    `/v1/messages/friends/requests/${requestId}/accept`,
    {},
  );
}

export async function rejectFriendRequest(
  requestId: string,
): Promise<FriendRequest> {
  return api.post<FriendRequest>(
    `/v1/messages/friends/requests/${requestId}/reject`,
    {},
  );
}

/** Cancel an outgoing pending request (from_user only). */
export async function cancelFriendRequest(requestId: string): Promise<void> {
  await api.delete(`/v1/messages/friends/requests/${requestId}`);
}

/** Remove an accepted friendship (DM history kept). */
export async function removeFriend(userId: string): Promise<void> {
  await api.delete(`/v1/messages/friends/${userId}`);
}

// --- Directory settings (discoverability + who-can-friend + who-can-DM) ---

function coerceDirectory(raw: DirectorySettings): DirectorySettings {
  return {
    discoverable: raw.discoverable ?? true,
    who_can_dm: normalizeWhoCanDm(raw.who_can_dm),
    who_can_friend: raw.who_can_friend ?? "anyone",
  };
}

export async function getDirectory(): Promise<DirectorySettings> {
  const raw = await api.get<DirectorySettings>("/v1/messages/directory");
  return coerceDirectory(raw);
}

export async function updateDirectory(
  patch: Partial<DirectorySettings>,
): Promise<DirectorySettings> {
  const body: UpdateDirectorySettingsRequest = { ...patch };
  const raw = await api.patch<DirectorySettings>(
    "/v1/messages/directory",
    body,
  );
  return coerceDirectory(raw);
}

// --- Blocking (任意搜人 护栏) ---

export async function listBlocks(): Promise<BlockedUser[]> {
  const res = await api.get<BlockListResponse>("/v1/messages/blocks");
  return res.data;
}

export async function blockUser(userId: string): Promise<void> {
  await api.post("/v1/messages/blocks", { user_id: userId });
}

export async function unblockUser(targetId: string): Promise<void> {
  await api.delete(`/v1/messages/blocks/${targetId}`);
}

// --- Error phrasing ---

/**
 * A user-facing zh message for a failed messaging call.
 *
 * The backend ships precise zh refusals (e.g. 对方仅允许联系人发起会话, 无法向该
 * 用户发送消息) in `{error:{message}}`, so surface that verbatim to keep wording
 * single-sourced; fall back to a status-based phrase, then a generic one. The
 * 404 default reads as "not found" rather than leaking chat existence (IDOR).
 */
export function messagingErrorMessage(
  err: unknown,
  fallback = "操作失败，请重试",
): string {
  if (err instanceof ApiError) {
    try {
      const body = JSON.parse(err.body) as { error?: { message?: string } };
      if (body.error?.message) return body.error.message;
    } catch {
      /* non-JSON body — fall through to status phrasing */
    }
    switch (err.status) {
      case 429:
        return "操作过于频繁，请稍后再试";
      case 403:
        return "无法向该用户发送消息";
      case 404:
        return "用户或会话不存在";
      case 422:
        return "请求无效，请检查后重试";
      default:
        return fallback;
    }
  }
  if (err instanceof NetworkError) return "网络连接中断，请重试";
  return fallback;
}
