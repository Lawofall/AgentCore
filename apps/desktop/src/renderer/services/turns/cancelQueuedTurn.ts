import { ApiError, api } from "@/services/api";
import { useConversationStore } from "@/stores/conversation";
import {
  type QueuedTurnEntry,
  useQueuedTurnsStore,
} from "@/stores/queuedTurns";
import { sendMidFlightMessage } from "./midFlight";

/** cancel HTTP 结果：成功才可 steer 重发；404 仅清条。 */
export type CancelQueuedTurnOutcome = "cancelled" | "already_gone";

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
 * 按项取消 FIFO 排队（``POST …/queued-turns/{queue_id}/cancel``）。
 * 成功或 404（已不在队）→ 立刻本地清条，不依赖 live ``turn_queue_cancelled``
 * （Stop 后常无该事件）。SSE 仍作多端同步（幂等清）。
 * Stop ≠ 取消排队。取消入口仅 QueuedTurnsBar。
 *
 * @returns ``cancelled`` = 服务端确认取消（可 steer 重发）；
 *          ``already_gone`` = 404 竞态/已出队（只清条、勿重发）。
 */
export async function cancelQueuedTurn(
  conversationId: string,
  queueId: string,
): Promise<CancelQueuedTurnOutcome> {
  try {
    await api.post(
      `/v1/conversations/${conversationId}/queued-turns/${queueId}/cancel`,
      {},
    );
    clearQueuedTurnLocally(conversationId, queueId);
    return "cancelled";
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      clearQueuedTurnLocally(conversationId, queueId);
      return "already_gone";
    }
    throw err;
  }
}

/**
 * 排队项「立刻插队」：取消该项 + 同内容 ``delivery=steer`` 重发。
 * 仅 cancel 确认为 ``cancelled`` 时重发；404/已出队只清条。
 * steer 降级回排队由 midFlight + messageStream toast 呈现，此处不伪装「已插入」。
 */
export async function steerQueuedTurn(
  conversationId: string,
  queueId: string,
): Promise<void> {
  const entry = useQueuedTurnsStore
    .getState()
    .list(conversationId)
    .find((e) => e.queueId === queueId);
  if (!entry) return;

  const { content } = entry;
  // 浅拷贝快照里的已收口载荷，保留 ``workspace_path`` 等驻留引用，不另造路径。
  const attachments = entry.attachments?.length
    ? entry.attachments.map((a) => ({ ...a }))
    : undefined;
  const agentMentions = entry.agentMentions?.length
    ? entry.agentMentions.map((a) => ({ ...a }))
    : undefined;
  const outcome = await cancelQueuedTurn(conversationId, queueId);
  if (outcome !== "cancelled") return;

  await sendMidFlightMessage(
    conversationId,
    content,
    attachments,
    "steer",
    agentMentions,
  );
}
