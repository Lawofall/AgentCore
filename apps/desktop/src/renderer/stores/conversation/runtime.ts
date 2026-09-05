import type { ConversationRuntime, Message } from "./types";

/** Runtime key for a draft chat that has no conversation id yet. */
export const DRAFT_KEY = "";

export const EMPTY_RUNTIME: ConversationRuntime = {
  messages: [],
  memoryUpdates: [],
  isGenerating: false,
  turnPhase: "idle",
  abort: null,
  error: null,
  retry: null,
  errorAction: null,
  messageFocus: null,
  hasMoreBefore: false,
  hasMoreAfter: false,
  loadingOlder: false,
  loadingNewer: false,
  pendingTurnWarning: null,
  pendingTraceId: null,
  toolStartedMs: {},
  executionVia: null,
  waitingForWorkspaceLock: false,
  waitingForDeskProvision: false,
};

export interface ConversationStateSlice {
  currentConversationId: string | null;
  byId: Record<string, ConversationRuntime>;
}

export const selectLastAssistantCostTotal = (
  messages: Message[],
): number | null => {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant")
      return messages[i].cost?.total ?? null;
  }
  return null;
};

export function lastAssistantMessageId(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return messages[i].id;
  }
  return null;
}

/** Stable turn / execution key: server turn id when stamped, else client bubble id. */
export function assistantProjectionId(message: Message): string {
  return message.serverMessageId ?? message.id;
}

/** Projection key of the last assistant (for live SSE → execution.byId). */
export function lastAssistantProjectionId(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant")
      return assistantProjectionId(messages[i]);
  }
  return null;
}

export function runtimeOf(
  state: ConversationStateSlice,
  conversationId?: string | null,
): ConversationRuntime {
  const key = conversationId ?? state.currentConversationId ?? DRAFT_KEY;
  return state.byId[key] ?? EMPTY_RUNTIME;
}

export function activeRuntime(
  state: ConversationStateSlice,
): ConversationRuntime {
  return runtimeOf(state);
}
