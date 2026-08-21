import { patchConversationCache } from "@/hooks/useConversations";
import { bindQueuedTurnUserId } from "@/services/turns/queuedTurnLocal";
import { useConversationStore } from "@/stores/conversation";
import type {
  CitationsPayload,
  EvidenceLedgerPayload,
  SSEEvent,
  TitleGeneratedPayload,
  TurnSavedPayload,
} from "@/types/events";
import type { DispatchContext } from "../types";

export function handleMetaEvent(
  event: SSEEvent,
  ctx: DispatchContext,
): boolean {
  const { conversationId } = ctx;

  switch (event.type) {
    case "title_generated": {
      const payload = event.payload as TitleGeneratedPayload;
      patchConversationCache(conversationId, {
        title: payload.title,
      });
      return true;
    }
    case "turn_saved": {
      const payload = event.payload as TurnSavedPayload;
      // 排队入场泡尚未绑服务端 id → 只改那条。否则空闲发送仍对最后一条 user 对账。
      // 禁止无条件扫最后一条 user（会改掉上一轮「你有什么功能」）。
      if (!bindQueuedTurnUserId(conversationId, payload.user_message_id)) {
        useConversationStore
          .getState()
          .reconcileLastTurn(payload.user_message_id, conversationId);
      }
      return true;
    }
    case "citations": {
      const payload = event.payload as CitationsPayload;
      useConversationStore
        .getState()
        .attachCitationsToLastMessage(payload.citations, conversationId);
      return true;
    }
    case "evidence_ledger": {
      const payload = event.payload as EvidenceLedgerPayload;
      useConversationStore
        .getState()
        .attachEvidenceLedgerToLastMessage(payload, conversationId);
      return true;
    }
    default:
      return false;
  }
}
