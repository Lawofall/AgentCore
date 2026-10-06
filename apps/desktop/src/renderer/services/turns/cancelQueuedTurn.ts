import { getConversations } from "@/hooks/useConversations";
import { ApiError, api } from "@/services/api";
import {
  getActiveSidecarTarget,
  getLastSidecarTarget,
  resolveConversationLocalTarget,
} from "@/services/sidecarRouting";
import type {
  OutgoingAgentMention,
  OutgoingAttachment,
} from "@/services/streamConversation";
import { ignoresCloudTurnActivity } from "@/stores/aiTurnActivity";
import { useConversationStore } from "@/stores/conversation";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import { clearQueuedTurnLocally } from "./queuedTurnLocal";

export {
  clearQueuedTurnLocally,
  insertQueuedTurnUserBubble,
} from "./queuedTurnLocal";

/** cancel HTTP / sidecar RPC 结果：成功才删泡；404 仅清条。 */
export type CancelQueuedTurnOutcome = "cancelled" | "already_gone";

/** 保存排队编辑：成功才留新正文；已开跑则回滚本地条。 */
export type EditQueuedTurnOutcome = "saved" | "already_gone";

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
 * 成功 → 清条并删泡。404 / ``not_found``（已开跑）→ 只清条，不删泡。
 *
 * @returns ``cancelled`` = 确认取消（并删泡）；
 *          ``already_gone`` = 竞态/已出队（只清条、勿删泡）。
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
      const outcome: CancelQueuedTurnOutcome =
        ack.status === "not_found" ? "already_gone" : "cancelled";
      clearQueuedTurnLocally(conversationId, queueId, {
        dropBubble: outcome === "cancelled",
      });
      return outcome;
    } catch (err) {
      if (isSidecarQueueNotFound(err)) {
        clearQueuedTurnLocally(conversationId, queueId, { dropBubble: false });
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
      clearQueuedTurnLocally(conversationId, queueId, { dropBubble: false });
      return "already_gone";
    }
    throw err;
  }
}

function snapshotQueue(conversationId: string) {
  return useQueuedTurnsStore
    .getState()
    .list(conversationId)
    .map((entry) => ({
      ...entry,
    }));
}

function restoreQueue(
  conversationId: string,
  prev: ReturnType<typeof snapshotQueue>,
): void {
  useQueuedTurnsStore.getState().replaceConversation(conversationId, prev);
}

/**
 * 拖动改 FIFO。先改本地顺序，失败再回滚。id 列表必须是当前集合。
 */
export async function reorderQueuedTurns(
  conversationId: string,
  queueIds: string[],
): Promise<void> {
  const prev = snapshotQueue(conversationId);
  useQueuedTurnsStore.getState().reorder(conversationId, queueIds);
  try {
    if (routesQueuedTurnToSidecar(conversationId)) {
      const target = await resolveSidecarQueueTarget(conversationId);
      if (!target) throw new Error("本地引擎未运行，无法调整排队顺序");
      await window.sidecarApi.reorderQueuedTurns({
        rootId: target.rootId,
        subpath: target.subpath,
        conversationId,
        queueIds,
      });
      return;
    }
    await api.post(`/v1/conversations/${conversationId}/queued-turns/reorder`, {
      queue_ids: queueIds,
    });
  } catch (err) {
    restoreQueue(conversationId, prev);
    throw err;
  }
}

/** 送给主管：已交给在跑的团队，或团队已不在（条目仍排队），或条目已不在队。 */
export type DeliverToCaptainOutcome =
  | "delivered"
  | "no_captain"
  | "already_gone";

/**
 * 把一条已排队的话送给在跑的主管，并把它撤出 FIFO。团队继续做。
 * 用户行留着，读到之前不进时间线。团队已散 → ``no_captain``，条不动。
 */
export async function deliverQueuedTurnToCaptain(
  conversationId: string,
  queueId: string,
): Promise<DeliverToCaptainOutcome> {
  const clearKept = () => {
    clearQueuedTurnLocally(conversationId, queueId, { dropBubble: false });
  };
  if (routesQueuedTurnToSidecar(conversationId)) {
    const target = await resolveSidecarQueueTarget(conversationId);
    if (!target) throw new Error("本地引擎未运行，无法送给主管");
    const ack = await window.sidecarApi.deliverQueuedTurnToCaptain({
      rootId: target.rootId,
      subpath: target.subpath,
      conversationId,
      queueId,
    });
    if (ack.status === "no_captain") return "no_captain";
    if (ack.status === "not_found") {
      clearKept();
      return "already_gone";
    }
    clearKept();
    return "delivered";
  }

  try {
    await api.post(
      `/v1/conversations/${conversationId}/queued-turns/${queueId}/to-captain`,
      {},
    );
    clearKept();
    return "delivered";
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) return "no_captain";
    if (
      err instanceof ApiError &&
      err.status === 404 &&
      (err.serverMessage ?? "").includes("排队项不存在")
    ) {
      clearKept();
      return "already_gone";
    }
    throw err;
  }
}

/**
 * 就地改正文、附件、@。先改本地条，失败再回滚。
 * 404 / ``not_found``（已开跑）回滚本地条并返回 ``already_gone``，不另排一条。
 */
export async function editQueuedTurn(
  conversationId: string,
  queueId: string,
  payload: {
    content: string;
    attachments: OutgoingAttachment[];
    agentMentions: OutgoingAgentMention[];
  },
): Promise<EditQueuedTurnOutcome> {
  const previous = useQueuedTurnsStore
    .getState()
    .patchPayload(conversationId, queueId, {
      content: payload.content,
      attachments: payload.attachments,
      agentMentions: payload.agentMentions,
    });
  if (!previous) return "already_gone";

  const restore = () => {
    useQueuedTurnsStore.getState().patchPayload(conversationId, queueId, {
      content: previous.content,
      attachments: previous.attachments,
      agentMentions: previous.agentMentions,
    });
  };

  try {
    if (routesQueuedTurnToSidecar(conversationId)) {
      const target = await resolveSidecarQueueTarget(conversationId);
      if (!target) throw new Error("本地引擎未运行，无法修改排队");
      const ack = await window.sidecarApi.editQueuedTurn({
        rootId: target.rootId,
        subpath: target.subpath,
        conversationId,
        queueId,
        content: payload.content,
        ...(payload.attachments.length > 0
          ? { attachments: payload.attachments }
          : {}),
        ...(payload.agentMentions.length > 0
          ? { agentMentions: payload.agentMentions }
          : {}),
      });
      if (ack.status === "not_found") {
        restore();
        return "already_gone";
      }
      return "saved";
    }
    await api.post(
      `/v1/conversations/${conversationId}/queued-turns/${queueId}/edit`,
      {
        content: payload.content,
        attachments: payload.attachments,
        agent_mentions: payload.agentMentions,
      },
    );
    return "saved";
  } catch (err) {
    restore();
    if (isSidecarQueueNotFound(err)) return "already_gone";
    throw err;
  }
}
