import { type RunStopAck, submitRunStop } from "@/services/runStop";
import { runtimeOf, useConversationStore } from "@/stores/conversation";
import { useRunStopPendingStore } from "@/stores/runStopPending";
import { interveneAckText } from "@agentcore/protocol-fold-kit";
import { toast } from "sonner";

/** Worker statuses that accept a mid-flight run-stop. */
export function isStoppableRunStatus(status: string): boolean {
  return status === "running" || status === "pending";
}

/**
 * Fire a structured run-stop (never guesses from free text). Marks honest
 * pending state; does **not** flip run status to cancelled locally.
 *
 * 回执由服务端给：只有引擎真的收下（`accepted`）才留着「停止请求中…」的在飞态并说
 * 「引擎将停下」。够不着时（驱动已退出 / run 不在当前计划里）把在飞态撤掉，照原话告诉
 * 用户什么都没发生——先前这里无论如何都报成功，是那句假承诺的出处。
 */
export async function requestRunStop(opts: {
  conversationId: string;
  executionId: string;
  runId?: string | null;
  /** Toast noun: node vs team. */
  scope: "node" | "team";
}): Promise<RunStopAck | null> {
  const { conversationId, executionId, runId = null, scope } = opts;
  const store = useRunStopPendingStore.getState();
  if (store.isPending(executionId, runId)) return null;

  store.markPending(executionId, runId);
  let ack: RunStopAck;
  try {
    ack = await submitRunStop(conversationId, { executionId, runId });
  } catch {
    store.clearPending(executionId, runId);
    toast.error("停止请求失败，请稍后重试");
    return null;
  }
  if (!ack.accepted) {
    store.clearPending(executionId, runId);
    // Composer 硬停已经把整轮标成 stopping；此时 run-stop 常拿到 no_live_drive
    // （驱动正在拆）。再警告「没有停下任何工作」像第二次停失败了。
    const wholeTurnStopping =
      runtimeOf(useConversationStore.getState(), conversationId).turnPhase ===
      "stopping";
    if (ack.reason === "no_live_drive" && wholeTurnStopping) {
      toast.info("整轮正在停下来");
    } else {
      toast.warning("没有停下任何工作", { description: interveneAckText(ack) });
    }
    return ack;
  }
  toast.success(scope === "team" ? "已请求停止任务" : "已请求停止此成员", {
    description:
      scope === "team"
        ? "队员将陆续停下；主 Agent 会留下来继续交代（不会结束整轮对话）。"
        : interveneAckText(ack),
  });
  return ack;
}
