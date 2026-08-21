import { getConversations } from "@/hooks/useConversations";
import { ApiError, api } from "@/services/api";
import {
  getActiveSidecarTarget,
  getLastSidecarTarget,
  resolveConversationLocalTarget,
} from "@/services/sidecarRouting";
import { ignoresCloudTurnActivity } from "@/stores/aiTurnActivity";
import { useConversationStore } from "@/stores/conversation";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import { sendMidFlightMessage } from "./midFlight";
import { clearQueuedTurnLocally } from "./queuedTurnLocal";

export {
  clearQueuedTurnLocally,
  insertQueuedTurnUserBubble,
} from "./queuedTurnLocal";

/** cancel HTTP / sidecar RPC 结果：成功才可 steer 重发；404 仅清条。 */
export type CancelQueuedTurnOutcome = "cancelled" | "already_gone";

function keepsLocalQueue(conversationId: string): boolean {
  const via =
    useConversationStore.getState().byId[conversationId]?.executionVia ?? null;
  const localContainerRootId =
    getConversations().find((c) => c.id === conversationId)
      ?.localContainerRootId ?? null;
  return ignoresCloudTurnActivity(via, localContainerRootId);
}

function routesQueuedTurnToSidecar(conversationId: string): boolean {
  return (
    getActiveSidecarTarget(conversationId) != null ||
    keepsLocalQueue(conversationId)
  );
}

async function resolveSidecarQueueTarget(
  conversationId: string,
): Promise<{ rootId: string; subpath: string } | null> {
  const live = getActiveSidecarTarget(conversationId);
  if (live) return live;
  const last = getLastSidecarTarget(conversationId);
  if (last) return last;
  try {
    return await resolveConversationLocalTarget(conversationId);
  } catch {
    return null;
  }
}

function isSidecarQueueNotFound(err: unknown): boolean {
  if (err instanceof ApiError && err.status === 404) return true;
  const raw = err instanceof Error ? err.message : String(err ?? "");
  return /not_found|404|排队项不存在/i.test(raw);
}

/**
 * 按项取消 FIFO 排队。sidecar live（或本机队）走 RPC；否则
 * ``POST …/queued-turns/{queue_id}/cancel``。
 * 成功或 404 / ``not_found``（已不在队）→ 立刻本地清条。
 *
 * @returns ``cancelled`` = 确认取消（可 steer 重发）；
 *          ``already_gone`` = 竞态/已出队（只清条、勿重发）。
 */
export async function cancelQueuedTurn(
  conversationId: string,
  queueId: string,
): Promise<CancelQueuedTurnOutcome> {
  if (routesQueuedTurnToSidecar(conversationId)) {
    const target = await resolveSidecarQueueTarget(conversationId);
    if (!target) {
      throw new Error("本地引擎未运行，无法取消排队");
    }
    try {
      const ack = await window.sidecarApi.cancelQueuedTurn({
        rootId: target.rootId,
        subpath: target.subpath,
        conversationId,
        queueId,
      });
      clearQueuedTurnLocally(conversationId, queueId);
      return ack.status === "not_found" ? "already_gone" : "cancelled";
    } catch (err) {
      if (isSidecarQueueNotFound(err)) {
        clearQueuedTurnLocally(conversationId, queueId);
        return "already_gone";
      }
      throw err;
    }
  }

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
