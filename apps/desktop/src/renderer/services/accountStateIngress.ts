import { getConversations } from "@/hooks/useConversations";
import { parseResumeSettledPayload } from "@/lib/resumeSettled";
import { type FulfillFrame, onFulfillFrame } from "@/services/fulfillStream";
import type {
  OutgoingAgentMention,
  OutgoingAttachment,
} from "@/services/streamConversation";
import {
  AI_ATTENTION_SNAPSHOT_TYPE,
  AI_ATTENTION_TYPE,
  type AiAttentionEvent,
  applyAiAttention,
  applyAiAttentionSnapshot,
} from "@/stores/aiAttention";
import {
  AI_TURN_ACTIVITY_SNAPSHOT_TYPE,
  AI_TURN_ACTIVITY_TYPE,
  applyAiTurnActivity,
  applyAiTurnActivitySnapshot,
  ignoresCloudTurnActivity,
} from "@/stores/aiTurnActivity";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import {
  type QueuedTurnEntry,
  TURN_QUEUE_ACCOUNT_SNAPSHOT_TYPE,
  TURN_QUEUE_SNAPSHOT_TYPE,
  useQueuedTurnsStore,
} from "@/stores/queuedTurns";

/**
 * 账号级状态帧 → 本地 store（设备长连接 `GET /v1/fulfill`）。
 *
 * 对话级订阅同时只留一条（每访问一个会话就多挂一条空闲 SSE 会吃光连接池），所以
 * 「另一个会话里发生的事」在本端没有任何显示流可走：队列是账号的，挂起卡也是账号的，
 * 哪些云对话还在跑、哪些对话停着等你也是账号的，它们在哪个对话里变化与用户此刻在看哪个对话无关。设备
 * 长连接正是按账号开、每台在线桌面一条的那条通道，这些状态就走它。
 *
 * 帧带的是**事实全量**（整条队列、结算后的卡面），不是「变了」信号——本端不再回头拉
 * 任何东西。连接播种 `turn_queue_account_snapshot` 整表替换云队（空表也 replace；
 * sidecar / 本地容器 key 保留）；增量仍是 `turn_queue_snapshot`。此前那三个对账
 * 模块（切会话 / 订阅重连时猜「可能漏了」再 GET）就此没有存在的理由。
 *
 * 只订云通道：sidecar 的履约推送是本机引擎在回合内发的 op 帧，不带账号态。
 */

let unsubscribe: (() => void) | null = null;

function keepsLocalQueue(conversationId: string): boolean {
  const via =
    useConversationStore.getState().byId[conversationId]?.executionVia ?? null;
  const localContainerRootId =
    getConversations().find((c) => c.id === conversationId)
      ?.localContainerRootId ?? null;
  return ignoresCloudTurnActivity(via, localContainerRootId);
}

function isAttachmentKind(
  value: unknown,
): value is NonNullable<OutgoingAttachment["kind"]> {
  return value === "file" || value === "dir" || value === "conversation";
}

/** 快照附件 → 发送载荷。原样保留 ``path`` / ``workspace_path``，禁止另造路径。 */
function mapQueuedAttachments(raw: unknown): OutgoingAttachment[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: OutgoingAttachment[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const a = row as Record<string, unknown>;
    const name = typeof a.name === "string" ? a.name : "";
    const path = typeof a.path === "string" ? a.path : "";
    if (!name || !path) continue;
    const mapped: OutgoingAttachment = {
      name,
      path,
      text: typeof a.text === "string" ? a.text : "",
      truncated: a.truncated === true,
    };
    if (isAttachmentKind(a.kind)) mapped.kind = a.kind;
    if (typeof a.conversation_id === "string" && a.conversation_id) {
      mapped.conversation_id = a.conversation_id;
    }
    if (a.binary === true) mapped.binary = true;
    if (typeof a.workspace_path === "string" && a.workspace_path) {
      mapped.workspace_path = a.workspace_path;
    }
    out.push(mapped);
  }
  return out.length > 0 ? out : undefined;
}

function mapQueuedMentions(raw: unknown): OutgoingAgentMention[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: OutgoingAgentMention[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const m = row as Record<string, unknown>;
    const agentId = typeof m.agent_id === "string" ? m.agent_id.trim() : "";
    const role = typeof m.role === "string" ? m.role.trim() : "";
    if (!agentId || !role) continue;
    out.push({ agent_id: agentId, role });
  }
  return out.length > 0 ? out : undefined;
}

function mapQueueItems(
  conversationId: string,
  items: unknown[],
): QueuedTurnEntry[] {
  const prevById = new Map(
    useQueuedTurnsStore
      .getState()
      .list(conversationId)
      .map((e) => [e.queueId, e]),
  );
  const depth = items.length;
  const next: QueuedTurnEntry[] = [];
  for (const raw of items) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    const queueId = typeof item.queue_id === "string" ? item.queue_id : "";
    if (!queueId) continue;
    const prev = prevById.get(queueId);
    const interjectionId =
      typeof item.interjection_id === "string"
        ? item.interjection_id.trim() || undefined
        : undefined;
    next.push({
      queueId,
      conversationId,
      content: typeof item.content === "string" ? item.content : "",
      position:
        typeof item.position === "number" ? item.position : next.length + 1,
      queueDepth: depth,
      interjectionId,
      // 出队插泡竞态：同 queue_id 仍在队时保留本地 messageId / degradedFrom。
      // 附件 / 点名以快照字段为真源，不再从旧条接回。
      messageId: prev?.messageId,
      degradedFrom: prev?.degradedFrom,
      attachments: mapQueuedAttachments(item.attachments),
      agentMentions: mapQueuedMentions(item.agent_mentions),
    });
  }
  return next;
}

function applyQueueSnapshot(payload: unknown): void {
  if (!payload || typeof payload !== "object") return;
  const p = payload as { conversation_id?: unknown; items?: unknown };
  const conversationId =
    typeof p.conversation_id === "string" ? p.conversation_id : "";
  // 增量只动这一条。缺字段 / 非数组丢帧；空 items 清该会话。禁止整表清空。
  if (!conversationId || !Array.isArray(p.items)) return;
  useQueuedTurnsStore
    .getState()
    .replaceConversation(
      conversationId,
      mapQueueItems(conversationId, p.items),
    );
}

function applyQueueAccountSnapshot(payload: unknown): void {
  if (!payload || typeof payload !== "object") return;
  const queues = (payload as { queues?: unknown }).queues;
  // 缺 queues / 非数组丢帧，不清现有表。空数组 = 只清云队。
  if (!Array.isArray(queues)) return;

  const cloud: Record<string, QueuedTurnEntry[]> = {};
  for (const raw of queues) {
    if (!raw || typeof raw !== "object") continue;
    const q = raw as { conversation_id?: unknown; items?: unknown };
    const conversationId =
      typeof q.conversation_id === "string" ? q.conversation_id : "";
    if (!conversationId || !Array.isArray(q.items)) continue;
    cloud[conversationId] = mapQueueItems(conversationId, q.items);
  }
  useQueuedTurnsStore.getState().replaceAll(cloud, keepsLocalQueue);
}

function applyPausedCardSettled(payload: unknown): void {
  const p = parseResumeSettledPayload(payload);
  if (!p) return;
  // 卡收成结果态（决策 + 落定时刻），壳一并丢掉——这帧就是「帧不在了」的证据。
  // 不碰气泡流：本端若正跟着那个对话，收口由它自己的回合流负责。
  useInteractionStore.getState().markResumeSettled({
    id: p.checkpoint_id,
    kind: p.kind,
    conversationId: p.conversation_id,
    messageId: p.message_id,
    decision: p.decision,
    decidedAt: p.decided_at,
    turnStatus: p.turn_status,
  });
  usePausedTurnStore.getState().removeByCheckpoint(p.checkpoint_id);
}

function onFrame(frame: FulfillFrame): void {
  if (frame.type === TURN_QUEUE_ACCOUNT_SNAPSHOT_TYPE) {
    applyQueueAccountSnapshot(frame.payload);
    return;
  }
  if (frame.type === TURN_QUEUE_SNAPSHOT_TYPE) {
    applyQueueSnapshot(frame.payload);
    return;
  }
  if (frame.type === "paused_card_settled") {
    applyPausedCardSettled(frame.payload);
    return;
  }
  if (frame.type === AI_TURN_ACTIVITY_SNAPSHOT_TYPE) {
    applyAiTurnActivitySnapshot(frame.payload);
    return;
  }
  if (frame.type === AI_TURN_ACTIVITY_TYPE) {
    applyAiTurnActivity(frame.payload);
    return;
  }
  if (frame.type === AI_ATTENTION_SNAPSHOT_TYPE) {
    applyAiAttentionSnapshot(frame.payload);
    return;
  }
  if (frame.type === AI_ATTENTION_TYPE) {
    applyFulfillAttention(frame.payload);
  }
}

function applyFulfillAttention(payload: unknown): void {
  if (!payload || typeof payload !== "object") return;
  const p = payload as Record<string, unknown>;
  if (p.state !== "required" && p.state !== "resolved") return;
  applyAiAttention({
    type: "ai_attention",
    state: p.state,
    conversation_id: p.conversation_id,
    turn_id: p.turn_id,
    interaction_id: p.interaction_id,
    kind: p.kind,
    title: p.title,
  } as AiAttentionEvent);
}

/** Subscribe once for the renderer lifetime (idempotent). Call from `main.tsx`. */
export function installAccountStateIngress(): void {
  if (unsubscribe) return;
  unsubscribe = onFulfillFrame(onFrame);
}

/** Test-only: drop the subscription. */
export function resetAccountStateIngressForTests(): void {
  unsubscribe?.();
  unsubscribe = null;
}
