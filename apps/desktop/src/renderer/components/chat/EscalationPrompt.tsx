import { useChatPaneId } from "@/lib/chatPane";
import { useActiveLastAssistantProjectionId } from "@/stores/conversation";
import { EscalationCards } from "./EscalationCard";

/** Pending 升级卡：和检查点 / 审批同一决策区（输入框上方，铬条 mx-4 mb-2）。过程时间线只留痕迹。 */
export function EscalationPrompt() {
  const conversationId = useChatPaneId();
  const messageId = useActiveLastAssistantProjectionId();
  if (!conversationId || !messageId) return null;
  return (
    <EscalationCards
      messageId={messageId}
      conversationId={conversationId}
      interactive
      pendingOnly
    />
  );
}
