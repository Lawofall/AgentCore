/**
 * 图头行动条数据 hook（批 R3）：把一个回合的待拍板（node 级升级/检查点 + execution
 * 级热闸）聚合成 {@link GraphPendingDecision}[]。内嵌 / 全屏两宿主共用。
 *
 * 自订 execution@playhead 的 pending Live sig——勿吃父树 Document epoch 快照，
 * 否则 escalate/checkpoint 变了行动条会冻住（节点 face 已 per-run 更新）。
 */

import { projectRuntime, useExecutionStore } from "@/stores/execution";
import {
  isHotGateInteractionKind,
  useInteractionStore,
} from "@/stores/interactions";
import { useMemo } from "react";
import {
  type GraphPendingDecision,
  type PendingInteractionRef,
  collectGraphPendingDecisions,
  graphPendingDecisionsLiveSig,
} from "./pendingDecisions";

export function useGraphPendingDecisions(
  conversationId: string | null,
  messageId: string | null,
): GraphPendingDecision[] {
  const pendingLiveSig = useExecutionStore((s) => {
    if (!messageId) return "";
    const rt = s.byId[messageId];
    if (!rt) return "";
    return graphPendingDecisionsLiveSig(projectRuntime(rt));
  });
  const byId = useInteractionStore((s) => s.byId);
  const interactions = useMemo<PendingInteractionRef[]>(() => {
    const out: PendingInteractionRef[] = [];
    if (!conversationId) return out;
    for (const e of byId.values()) {
      if (e.conversationId !== conversationId) continue;
      // 宽松按回合匹配：交互缺 messageId（会话级）时也纳入（与 matchesMessage 一致）。
      if (messageId && e.messageId && e.messageId !== messageId) continue;
      if (e.status !== "pending" && e.status !== "submitting") continue;
      // Store slice = hot ∧ pausesTurn (today: approval). Not attention /
      // not all hot — node escalate/checkpoint come from execution, not here.
      if (isHotGateInteractionKind(e.kind)) {
        out.push({ kind: e.kind, id: e.id });
      }
    }
    return out;
  }, [byId, conversationId, messageId]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: pendingLiveSig is intentional invalidation key
  return useMemo(() => {
    if (!messageId) return [];
    const rt = useExecutionStore.getState().byId[messageId];
    const execution = rt ? projectRuntime(rt) : null;
    return collectGraphPendingDecisions(execution, interactions);
  }, [pendingLiveSig, interactions, messageId]);
}

export type { GraphPendingDecision };
