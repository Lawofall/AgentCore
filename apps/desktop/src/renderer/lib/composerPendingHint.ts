import { listVisibleColdResumes } from "@/services/resume";
import {
  isHotGateInteractionKind,
  useInteractionStore,
} from "@/stores/interactions";

/** Persistent composer hint while a decision card is waiting (弱提示 · 不强拦). */
export const COMPOSER_PENDING_HINT =
  "当前有待你确认的事项；另开一轮，确认卡仍保留";

/** Confirm copy on first send while pending (同会话确认一次后不再弹). */
export const COMPOSER_PENDING_SEND_CONFIRM =
  "仍有待确认事项。发送将另开一轮，确认卡仍保留，确定继续？";

/** Conversations where the user already confirmed「仍要发送」this session. */
const sendDespitePendingAcks = new Set<string>();

/** True when the conversation has a gate card awaiting the user (hot-gate or visible cold resume). */
export function conversationHasPendingDecision(
  conversationId: string,
): boolean {
  const byId = useInteractionStore.getState().byId;
  for (const e of byId.values()) {
    if (e.conversationId !== conversationId) continue;
    if (e.status !== "pending" && e.status !== "submitting") continue;
    if (isHotGateInteractionKind(e.kind)) {
      return true;
    }
  }
  // Cold clickability = ResumePrompt gate (journal / noted settlement).
  return listVisibleColdResumes(conversationId).length > 0;
}

export function hasAckedSendDespitePending(conversationId: string): boolean {
  return sendDespitePendingAcks.has(conversationId);
}

export function ackSendDespitePending(conversationId: string): void {
  sendDespitePendingAcks.add(conversationId);
}

/** Test / session reset helper. */
export function resetSendDespitePendingAcks(): void {
  sendDespitePendingAcks.clear();
}

/**
 * Gate for the weak confirm before a new turn: pending cards + not yet acked.
 * Callers pass `!isGenerating` so mid-flight 插话 / 正规续跑卡提交不受影响.
 */
export function shouldConfirmSendDespitePending(
  conversationId: string,
): boolean {
  return (
    conversationHasPendingDecision(conversationId) &&
    !hasAckedSendDespitePending(conversationId)
  );
}

/**
 * Run {@link window.confirm} when needed; returns false if the user backs out.
 * On confirm, remembers the ack for this conversation for the rest of the session.
 */
export function confirmSendDespitePendingIfNeeded(
  conversationId: string | null | undefined,
  isGenerating: boolean,
): boolean {
  if (!conversationId || isGenerating) return true;
  if (!shouldConfirmSendDespitePending(conversationId)) return true;
  if (!window.confirm(COMPOSER_PENDING_SEND_CONFIRM)) return false;
  ackSendDespitePending(conversationId);
  return true;
}
