import {
  mapQueuedAttachments,
  mapQueuedMentions,
} from "@/services/queuedTurnMap";
import type {
  OutgoingAgentMention,
  OutgoingAttachment,
} from "@/services/streamConversation";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import {
  type QueuedTurnEntry,
  useQueuedTurnsStore,
} from "@/stores/queuedTurns";

type QueuedBubble = {
  conversationId: string;
  queueId: string;
  localId: string;
  bound: boolean;
};

/** 独立于 QueuedTurnsBar：条会被 fulfill 空快照清掉，出队插泡仍须幂等。 */
const queuedBubbles = new Map<string, QueuedBubble>();

function queueKey(conversationId: string, queueId: string): string {
  return `${conversationId}\n${queueId}`;
}

function peekQueuedBubble(
  conversationId: string,
  queueId: string,
): QueuedBubble | null {
  const entry = queuedBubbles.get(queueKey(conversationId, queueId));
  if (!entry) return null;
  const exists = getRuntime(conversationId).messages.some(
    (m) => m.id === entry.localId,
  );
  if (!exists) {
    queuedBubbles.delete(queueKey(conversationId, queueId));
    return null;
  }
  return entry;
}

/** 把 ``queue_id`` 钉到已入场的用户泡上，出队 ``turn_queue_started`` 不再二次插泡。 */
export function registerQueuedTurnUserBubble(
  conversationId: string,
  queueId: string,
  localId: string,
): void {
  const qid = queueId.trim();
  const lid = localId.trim();
  if (!conversationId || !qid || !lid) return;
  queuedBubbles.set(queueKey(conversationId, qid), {
    conversationId,
    queueId: qid,
    localId: lid,
    bound: false,
  });
}

/**
 * 生成中再发 ack 后立刻入主时间线（排队 / 插队同一条用户泡）。
 * ``queueId`` 有值时登记出队幂等键。同 ``id`` 已在列表则只登记、不双泡。
 */
export function paintMidFlightUserBubble(
  conversationId: string,
  args: {
    id?: string;
    content: string;
    attachments?: OutgoingAttachment[];
    agentMentions?: OutgoingAgentMention[];
    queueId?: string;
  },
): string {
  const id = (args.id ?? "").trim() || crypto.randomUUID();
  const exists = getRuntime(conversationId).messages.some((m) => m.id === id);
  if (!exists) {
    const attachments = args.attachments;
    const agentMentions = args.agentMentions;
    useConversationStore.getState().addMessage(
      {
        id,
        role: "user",
        content: args.content,
        createdAt: new Date().toISOString(),
        executionId: null,
        isStreaming: false,
        attachments:
          attachments && attachments.length > 0
            ? attachments.map((a, i) => ({
                id: `mf-att-${i}`,
                name: a.name,
                path: a.path,
                truncated: a.truncated,
                kind: a.kind,
                conversationId: a.conversation_id,
                documentId: a.document_id,
                workspacePath: a.workspace_path,
              }))
            : undefined,
        agentMentions:
          agentMentions && agentMentions.length > 0
            ? agentMentions.map((a) => ({
                agentId: a.agent_id,
                role: a.role,
              }))
            : undefined,
      },
      conversationId,
    );
  }
  if (args.queueId) {
    registerQueuedTurnUserBubble(conversationId, args.queueId, id);
  }
  return id;
}

/**
 * 本地清排队条（幂等）。有关联 messageId 时顺带删泡；无泡则只清条。
 * HTTP 取消成功 / 404、以及 SSE ``turn_queue_cancelled`` 共用。
 */
export function clearQueuedTurnLocally(
  conversationId: string,
  queueId: string,
): QueuedTurnEntry | null {
  const removed = useQueuedTurnsStore
    .getState()
    .remove(conversationId, queueId);
  if (removed?.messageId) {
    useConversationStore
      .getState()
      .removeMessage(removed.messageId, conversationId);
  }
  return removed;
}

/**
 * ``turn_queue_started`` 出队开跑：发送端 ack 已入场则只绑 ``queue_id``；
 * 他端 / 无泡才从帧 payload 插用户泡。按 ``conversationId+queue_id`` 防双泡。
 */
export function insertQueuedTurnUserBubble(
  conversationId: string,
  payload: unknown,
): string | null {
  const p =
    payload && typeof payload === "object"
      ? (payload as Record<string, unknown>)
      : {};
  const queueId = typeof p.queue_id === "string" ? p.queue_id.trim() : "";
  if (!queueId) return null;

  const existing = peekQueuedBubble(conversationId, queueId);
  if (existing) return existing.localId;

  const hasContentField = typeof p.content === "string";
  const attachments = mapQueuedAttachments(p.attachments);
  const agentMentions = mapQueuedMentions(p.agent_mentions);
  if (!hasContentField && !attachments && !agentMentions) return null;

  const id = crypto.randomUUID();
  useConversationStore.getState().addMessage(
    {
      id,
      role: "user",
      content: hasContentField ? (p.content as string) : "",
      createdAt: new Date().toISOString(),
      executionId: null,
      isStreaming: false,
      attachments:
        attachments && attachments.length > 0
          ? attachments.map((a, i) => ({
              id: `mf-att-${i}`,
              name: a.name,
              path: a.path,
              truncated: a.truncated,
              kind: a.kind,
              conversationId: a.conversation_id,
              documentId: a.document_id,
              workspacePath: a.workspace_path,
            }))
          : undefined,
      agentMentions:
        agentMentions && agentMentions.length > 0
          ? agentMentions.map((a) => ({
              agentId: a.agent_id,
              role: a.role,
            }))
          : undefined,
    },
    conversationId,
  );
  queuedBubbles.set(queueKey(conversationId, queueId), {
    conversationId,
    queueId,
    localId: id,
    bound: false,
  });
  return id;
}

/**
 * ``turn_saved``：若本会话有尚未绑服务端 id 的排队入场泡，只改那条。
 * 气泡 id 与 ``queuedTurns.messageId``（「排队中」徽标 / 取消删泡）一并换成服务端 id。
 * @returns 已绑上 → 调用方不得再 ``reconcileLastTurn``（会改掉上一轮最后一条 user）。
 */
export function bindQueuedTurnUserId(
  conversationId: string,
  userMessageId: string,
): boolean {
  const serverId = userMessageId.trim();
  if (!serverId) return false;

  let hit: QueuedBubble | undefined;
  for (const entry of queuedBubbles.values()) {
    if (entry.conversationId === conversationId && !entry.bound) {
      hit = entry;
      break;
    }
  }
  if (!hit) return false;

  const exists = getRuntime(conversationId).messages.some(
    (m) => m.id === hit.localId,
  );
  if (!exists) {
    queuedBubbles.delete(queueKey(hit.conversationId, hit.queueId));
    return false;
  }

  if (hit.localId !== serverId) {
    useConversationStore
      .getState()
      .updateMessage(hit.localId, { id: serverId }, conversationId);
  }
  hit.localId = serverId;
  hit.bound = true;

  const queued = useQueuedTurnsStore.getState();
  const entry = queued
    .list(conversationId)
    .find((e) => e.queueId === hit.queueId);
  if (entry && entry.messageId !== serverId) {
    queued.upsert({ ...entry, messageId: serverId });
  }
  return true;
}

/** Test-only: drop in-memory queued-entry bubble keys. */
export function resetQueuedTurnLocalForTests(): void {
  queuedBubbles.clear();
}
