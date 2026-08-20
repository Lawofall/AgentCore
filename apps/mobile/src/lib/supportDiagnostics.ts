import type {
  ErrorPayload,
  MessageStartPayload,
  SSEEvent,
  TurnSavedPayload,
} from "@agentcore/contract-types";
import {
  type SupportDiagnosticIds,
  supportDiagnosticExtrasFromError,
} from "@agentcore/protocol-fold-kit";

export {
  formatSupportDiagnosticText,
  supportDiagnosticExtrasFromError,
  type SupportDiagnosticIds,
} from "@agentcore/protocol-fold-kit";

/** Preceding user bubble id for an assistant message (regenerate / 排查包). */
export function precedingUserMessageId(
  messages: ReadonlyArray<{ id: string; role: string }>,
  assistantMessageId: string,
): string | null {
  const idx = messages.findIndex((m) => m.id === assistantMessageId);
  if (idx <= 0) return null;
  for (let i = idx - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].id;
  }
  return null;
}

/** Pull support ids from a live/history SSE journal (message_start + first run_plan). */
export function extractSupportIdsFromEvents(events: readonly SSEEvent[]): {
  messageId?: string;
  userMessageId?: string;
  traceId?: string;
  executionId?: string;
} {
  let messageId: string | undefined;
  let userMessageId: string | undefined;
  let traceId: string | undefined;
  let executionId: string | undefined;
  for (const ev of events) {
    if (ev.type === "message_start") {
      const p = ev.payload as MessageStartPayload;
      if (p.message_id) messageId = p.message_id;
      if (p.trace_id) traceId = p.trace_id;
    } else if (!executionId && ev.type === "run_plan") {
      const id = (ev.payload as { execution_id?: string }).execution_id?.trim();
      if (id) executionId = id;
    } else if (ev.type === "turn_saved") {
      const id = (ev.payload as TurnSavedPayload).user_message_id?.trim();
      if (id) userMessageId = id;
    }
  }
  return { messageId, userMessageId, traceId, executionId };
}

/**
 * Single builder for every「复制排查包」entry (bubble / footer / page bar).
 * Same events + conversationId → identical paste text.
 */
export function supportIdsFromEvents(
  conversationId: string | null | undefined,
  events: readonly SSEEvent[],
  rest?: {
    messageId?: string | null;
    userMessageId?: string | null;
    traceId?: string | null;
  },
): SupportDiagnosticIds {
  const ids = extractSupportIdsFromEvents(events);
  let extras: ReturnType<typeof supportDiagnosticExtrasFromError> = {};
  for (const ev of events) {
    if (ev.type !== "error") continue;
    extras = supportDiagnosticExtrasFromError(ev.payload as ErrorPayload);
  }
  return {
    conversationId: conversationId || undefined,
    messageId: rest?.messageId || ids.messageId,
    userMessageId: rest?.userMessageId || ids.userMessageId,
    traceId: rest?.traceId || ids.traceId,
    executionId: ids.executionId,
    ...extras,
  };
}
