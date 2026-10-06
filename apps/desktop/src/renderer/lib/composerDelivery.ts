import { useChatPaneSliceKey } from "@/lib/chatPane";
import {
  getRuntime,
  lastAssistantProjectionId,
  runtimeOf,
  useConversationStore,
} from "@/stores/conversation";
import {
  execRuntime,
  isConversationExecutionLive,
  useExecutionStore,
} from "@/stores/execution";

export type MessageDelivery = "steer" | "queue";

/**
 * 协调活跃 ≈ 当前回合已有团队 plan（run_plan）。
 * 发送路径 snapshot 即可；按钮布局须走 {@link useCoordinationActive}。
 */
export function isCoordinationActive(
  conversationId: string | null | undefined,
): boolean {
  if (!conversationId) return false;
  const key = lastAssistantProjectionId(getRuntime(conversationId).messages);
  if (!key) return false;
  return Boolean(useExecutionStore.getState().byId[key]?.plan);
}

/** 订阅最新助手泡是否已有 plan；ingestPlan 后按钮布局要跟着换，不能只 getState。 */
export function useCoordinationActive(): boolean {
  const paneKey = useChatPaneSliceKey();
  const lastKey = useConversationStore((s) =>
    lastAssistantProjectionId(runtimeOf(s, paneKey).messages),
  );
  return useExecutionStore((s) =>
    lastKey ? Boolean(s.byId[lastKey]?.plan) : false,
  );
}

/**
 * 这桌还有活队：协作图仍在转（含主管已写出一段但仍在听团）。
 * 发送门跟队走，不跟「助手泡看起来写完了」。
 */
export function isLiveCoordinatingTurn(
  conversationId: string | null | undefined,
): boolean {
  if (!conversationId) return false;
  const key = lastAssistantProjectionId(getRuntime(conversationId).messages);
  if (!key) return false;
  const rt = useExecutionStore.getState().byId[key];
  return rt ? isConversationExecutionLive(rt) : false;
}

/** 订阅最新助手泡的协作图是否还在转。 */
export function useLiveCoordinatingTurn(): boolean {
  const paneKey = useChatPaneSliceKey();
  const lastKey = useConversationStore((s) =>
    lastAssistantProjectionId(runtimeOf(s, paneKey).messages),
  );
  return useExecutionStore((s) =>
    lastKey ? isConversationExecutionLive(execRuntime(s, lastKey)) : false,
  );
}

function assistantHasRunningTool(
  messages: readonly {
    role: string;
    process?: readonly { kind: string; status?: string }[];
  }[],
): boolean {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role !== "assistant") continue;
    return (message.process ?? []).some(
      (step) => step.kind === "tool" && step.status === "running",
    );
  }
  return false;
}

/** 经典回合还有正在执行的工具步，下一工具步顶可以注入。 */
export function classicToolStepOpen(
  conversationId: string | null | undefined,
): boolean {
  if (!conversationId) return false;
  return assistantHasRunningTool(getRuntime(conversationId).messages);
}

/** 订阅最新助手泡是否有正在执行的工具步。 */
export function useClassicToolStepOpen(): boolean {
  const paneKey = useChatPaneSliceKey();
  return useConversationStore((s) =>
    assistantHasRunningTool(runtimeOf(s, paneKey).messages),
  );
}

/**
 * 生成中 Ctrl/Cmd+Enter：
 * - 队还在，或经典回合有正在执行的工具步 → steer
 * - 其余（含经典散文）→ queue，与 Enter 相同
 */
export function resolveOccupiedShortcutDelivery(
  conversationId: string | null | undefined,
): MessageDelivery {
  if (isLiveCoordinatingTurn(conversationId)) return "steer";
  if (classicToolStepOpen(conversationId)) return "steer";
  return "queue";
}

/**
 * 默认 delivery：
 * - 空闲 → steer（开新回合）
 * - 生成中（含团队还在听）→ queue（Enter / 发送钮；挂在输入框上方）
 */
export function resolveDefaultDelivery(
  isGenerating: boolean,
  _conversationId?: string | null,
): MessageDelivery {
  if (!isGenerating) return "steer";
  return "queue";
}
