import {
  getRuntime,
  lastAssistantProjectionId,
  useConversationStore,
} from "@/stores/conversation";
import {
  type Execution,
  isDebate,
  projectRuntime,
  useExecutionStore,
} from "@/stores/execution";
import { useMemo } from "react";

/** 辩论进行中主框占位：对这场说话，不是开新回合 / 插 CEO。 */
export const COMPOSER_DEBATE_STEER_PLACEHOLDER = "对这场说话，下一轮生效…";

/**
 * 当前会话最后一条助手消息的 execution 是辩论且未收场。
 *
 * 未收场 = 尚无 `debate_result`（`execution.debate == null`）。进行中 = running /
 * paused（Stop / 失败收口后主框恢复 sendTurn，避免关窗后仍走辩论插话）。
 * `planType === "debate"` 盖住辩手 run 尚未打 stance 标签的开场窗口。
 */
export function isLiveUnsettledDebate(
  execution: Execution | null | undefined,
): execution is Execution {
  if (!execution || execution.debate != null) return false;
  if (execution.status !== "running" && execution.status !== "paused") {
    return false;
  }
  return isDebate(execution) || execution.planType === "debate";
}

/** 主框辩论插话目标：会话 id + 主持人捞的 execution id。没有则不是辩论进行中。 */
export function liveDebateSteerTarget(
  conversationId: string | null | undefined,
): { conversationId: string; executionId: string } | null {
  if (!conversationId) return null;
  const key = lastAssistantProjectionId(getRuntime(conversationId).messages);
  if (!key) return null;
  const rt = useExecutionStore.getState().byId[key];
  if (!rt) return null;
  const execution = projectRuntime(rt);
  if (!isLiveUnsettledDebate(execution)) return null;
  return { conversationId, executionId: execution.id };
}

/** 主框 chrome：进行中隐藏排队/插队、出示结论。 */
export function useLiveDebateSteer(): boolean {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const lastKey = useConversationStore((s) => {
    const id = s.currentConversationId;
    if (!id) return null;
    return lastAssistantProjectionId(s.byId[id]?.messages ?? []);
  });
  const rt = useExecutionStore((s) =>
    conversationId && lastKey ? s.byId[lastKey] : undefined,
  );
  const execution = useMemo(() => (rt ? projectRuntime(rt) : null), [rt]);
  return isLiveUnsettledDebate(execution);
}
