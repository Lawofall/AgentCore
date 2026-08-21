import {
  mapQueuedAttachments,
  mapQueuedMentions,
} from "@/services/queuedTurnMap";
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
 * ``turn_queue_started`` 出队开跑：从帧 payload 的 content / attachments /
 * agent_mentions 幂等插用户泡（不从 QueuedTurnsBar 抄；条可已被空快照清掉）。
 * midFlight 与 messageStream 共用，按 ``conversationId+queue_id`` 防双泡。
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
  return true;
}

/** Test-only: drop in-memory queued-entry bubble keys. */
export function resetQueuedTurnLocalForTests(): void {
  queuedBubbles.clear();
}
