import { patchConversationCache } from "@/hooks/useConversations";
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
      useConversationStore
        .getState()
        .reconcileLastTurn(payload.user_message_id, conversationId);
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
