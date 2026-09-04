import { invalidateAllFolderSharing } from "@/hooks/useFolderSharing";
import { clientHeaders } from "@/lib/clientBuildInfo";
import { queryClient } from "@/lib/queryClient";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import { notifyInfo } from "@/lib/toast";
import {
  BASE_URL,
  captureCsrf,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import { toMemoryUpdate } from "@/services/messages";
import type {
  ChatMessageDetail,
  FriendRequest,
  FriendRequestAction,
} from "@/services/messaging";
import {
  type AiAttentionEvent,
  applyAiAttention,
  clearAiAttention,
} from "@/stores/aiAttention";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversation";
import { useMessagingStore } from "@/stores/messaging";
import type { components } from "@/types/api.generated";

/**
 * Per-user realtime firehose client for the 消息 page (消息IM.md §四).
 *
 * One long-lived `GET /v1/realtime` SSE stream carries every chat's new messages,
 * presence transitions, and chat-list membership changes (`chat_changed`) to this
 * user (server→client; sending stays POST). It runs at the app shell for the whole
 * authenticated session — not the 消息 page — so unread badges, online dots,
 * incoming messages, and newly joined chats update even while the user is on the
 * 对话 page.
 *
 * SSE can't refresh a token mid-stream, so this mirrors the POST stream's policy
 * (streamConversation.ts): on a 401, refresh once and reconnect; otherwise drop
 * to login. Transport drops reconnect with capped exponential backoff, and every
 * (re)connect triggers a catch-up (refetch the chat list + reload the open
 * thread) so anything missed while disconnected is re-synced (离线补偿).
 */

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

let running = false;
let controller: AbortController | null = null;
let reconnectTimer: number | null = null;
let attempts = 0;

type StreamOutcome = "reconnect" | "stop";

interface ChatMessageEvent {
  type: "chat_message";
  chat_id: string;
  message: ChatMessageDetail;
}

interface ChatMessageUpdatedEvent {
  type: "chat_message_updated";
  chat_id: string;
  message: ChatMessageDetail;
}

/** 协作桌：someone invited me (`folder_invite`) or membership / files changed
 * (`folder_changed`). Firehose is a nudge only — durable path is
 * `GET /v1/folders/invites/pending`. */
interface FolderInviteEvent {
  type: "folder_invite";
  folder_id?: string;
  folder_name?: string;
}

/** IM presence: a co-chat user connected/disconnected their firehose. */
interface PresenceEvent {
  type: "presence";
  user_id: string;
  online: boolean;
}

/** Friend-request lifecycle (消息IM.md §9.3) — refresh inbox / profile relation. */
interface FriendRequestEvent {
  type: "friend_request";
  action: FriendRequestAction;
  request: FriendRequest;
}

/** Chat list membership / activation nudge — peer created a DM, I was added to a
 * group, or a message request became active. Sparse; full list refetch is enough. */
interface ChatChangedEvent {
  type: "chat_changed";
  chat_id: string;
  reason: "created" | "member_added" | "activated";
}

/** 记忆更新对话内可见 (§1.6): one offline-consolidation pass that changed a memory
 * file. `update` (the conversation-tail card payload) is present whenever the pass
 * recorded a row; its shape mirrors the REST `MemoryUpdateView` so {@link toMemoryUpdate}
 * maps it. Absent on older/edge passes — semantic / quota still fall back to a
 * heads-up toast. */
interface MemoryUpdatedEvent {
  type: "memory_updated";
  conversation_id: string;
  kind?: components["schemas"]["MemoryUpdateView"]["kind"];
  update?: components["schemas"]["MemoryUpdateView"] & {
    conversation_id?: string;
  };
}

type MemoryUpdateKind = components["schemas"]["MemoryUpdateView"]["kind"];

/** Cross-conversation heads-up for `memory_updated`. `null` = no toast: the inline
 * card (if this conversation is open) and the 记忆动态 feed already cover it.
 * Toast only when the user is away from the source conversation. */
export function memoryUpdatedToastCopy(
  kind: MemoryUpdateKind,
  cardShown: boolean,
): string | null {
  if (cardShown) return null;
  if (kind === "quota") {
    // Never claim a write that was refused (审计 CTX-A2).
    return "常驻条目已满，有内容没能记下";
  }
  return "AI 刚刚更新了你的记忆";
}

/** Re-sync state that may have changed while disconnected. */
function catchUp(): void {
  const store = useMessagingStore.getState();
  void store.fetchChats();
  void (async () => {
    await store.fetchFriends();
    await store.fetchFriendRequests();
  })();
  if (store.activeChatId) void store.loadMessages(store.activeChatId);
}

/** Parse one SSE frame (lines split by \n) and dispatch a chat_message. */
function handleFrame(frame: string): void {
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return; // heartbeat comment or event-only frame
  try {
    const event = JSON.parse(dataLines.join("\n")) as { type?: string };
    if (event.type === "chat_message") {
      const e = event as ChatMessageEvent;
      useMessagingStore.getState().applyIncoming(e.chat_id, e.message);
    } else if (event.type === "chat_message_updated") {
      const e = event as ChatMessageUpdatedEvent;
      useMessagingStore.getState().applyMessageUpdated(e.chat_id, e.message);
    } else if (event.type === "memory_updated") {
      // Offline consolidation nudge (off the turn path). 记忆更新对话内可见 (§1.6):
      // live-append the card to the source conversation (no-op if unloaded — fetched on
      // next open). semantic = 偏好/画像/主题 rewrite; quota = always-pool refusal.
      const e = event as MemoryUpdatedEvent;
      const conv = useConversationStore.getState();
      const kind = e.update?.kind ?? e.kind ?? "semantic";
      if (e.update && e.conversation_id) {
        conv.addMemoryUpdate(
          toMemoryUpdate({
            ...e.update,
            kind,
          }),
          e.conversation_id,
        );
      }
      // 记忆动态 feed live-refresh: mark the cross-conversation「最近更新」query stale so an
      // OPEN MemoryUpdatesView refetches at once (a closed one just refetches on next open —
      // free).
      void queryClient.invalidateQueries({ queryKey: ["memory-updates"] });
      const cardShown =
        !!(e.update && e.conversation_id) &&
        conv.currentConversationId === e.conversation_id;
      const toastCopy = memoryUpdatedToastCopy(kind, cardShown);
      if (toastCopy) notifyInfo(toastCopy);
    } else if (event.type === "folder_invite") {
      const e = event as FolderInviteEvent;
      invalidateAllFolderSharing();
      notifyInfo(
        e.folder_name
          ? `你被邀请加入「${e.folder_name}」`
          : "你有新的协作桌邀请",
      );
    } else if (event.type === "folder_changed") {
      invalidateAllFolderSharing();
    } else if (event.type === "presence") {
      const e = event as PresenceEvent;
      if (e.user_id) {
        useMessagingStore.getState().applyPresence(e.user_id, !!e.online);
      }
    } else if (event.type === "friend_request") {
      const e = event as FriendRequestEvent;
      const action = e.action ?? "cancelled";
      const request = e.request ?? ({ id: "" } as FriendRequest);
      useMessagingStore.getState().applyFriendRequestEvent({ action, request });
      const myId = useAuthStore.getState().user?.id;
      if (action === "created" && myId && e.request?.to_user_id === myId) {
        notifyInfo("你收到一条好友申请");
      } else if (
        action === "accepted" &&
        myId &&
        e.request?.from_user_id === myId
      ) {
        notifyInfo("对方已同意好友申请");
      }
    } else if (event.type === "chat_changed") {
      const e = event as ChatChangedEvent;
      if (e.chat_id) {
        void useMessagingStore.getState().fetchChats();
      }
    } else if (event.type === "ai_attention") {
      // 「某个对话在等你」(云对话多端同权 B2 · L1)：AI 停在阻塞卡上时 required、任一端
      // 放行后 resolved。只送信号不送内容——落进 AiAttentionStore 点亮侧栏「等你」灯，
      // 跨对话提醒（teamActivityNotifications）另订该 store 弹一条可跳转的提示。
      applyAiAttention(event as AiAttentionEvent);
    }
    // "ready" and any other event types: no-op here.
  } catch {
    /* malformed frame — skip */
  }
}

/** Open the stream and pump frames until it ends; returns how to proceed. */
async function runStream(signal: AbortSignal): Promise<StreamOutcome> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/v1/realtime`, {
      method: "GET",
      credentials: sessionCredentials(),
      headers: {
        Accept: "text/event-stream",
        ...clientHeaders(),
        ...bearerAuthHeader(),
      },
      signal,
    });
    captureCsrf(response); // 全会话长连接，每次重连都是一次刷新令牌的机会
  } catch {
    return "reconnect"; // transport failure (offline / aborted)
  }

  if (response.status === 401) {
    const outcome = await tryRefresh();
    if (outcome === "renewed" || outcome === "transient") return "reconnect";
    notifyUnauthorized();
    return "stop";
  }
  if (!response.ok || !response.body) return "reconnect";

  // Connected: reset backoff and re-sync anything missed while disconnected.
  attempts = 0;
  catchUp();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) handleFrame(frame);
    }
  } catch {
    return "reconnect"; // read error (incl. abort — caller checks the signal)
  }
  return "reconnect"; // server closed the stream
}

function scheduleReconnect(): void {
  if (!running || reconnectTimer !== null) return;
  const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempts, RECONNECT_MAX_MS);
  attempts += 1;
  reconnectTimer = window.setTimeout(
    () => {
      reconnectTimer = null;
      void connect();
    },
    delay + Math.random() * 500,
  );
}

async function connect(): Promise<void> {
  if (!running) return;
  const ac = new AbortController();
  controller = ac;
  let outcome: StreamOutcome = "reconnect";
  try {
    outcome = await runStream(ac.signal);
  } catch {
    outcome = "reconnect";
  }
  if (ac.signal.aborted || !running) return;
  if (outcome === "stop") {
    running = false;
    return;
  }
  scheduleReconnect();
}

/** Open the firehose for the current session (idempotent). */
export function startRealtime(): void {
  if (running) return;
  running = true;
  attempts = 0;
  void connect();
}

/** Close the firehose and cancel any pending reconnect (idempotent). */
export function stopRealtime(): void {
  running = false;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  controller?.abort();
  controller = null;
  // 「等你」提醒是本账号会话内的东西：管子一关（登出 / 关窗）即作废，别留给下一个账号。
  clearAiAttention();
}
