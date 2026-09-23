import { logEvent } from "@/lib/log";
import { isMessageWindowResident } from "./messageWindowWrite";
import { createPatchConversation } from "./patchConversation";
import { DRAFT_KEY } from "./runtime";
import type {
  ConversationGet,
  ConversationSet,
  ConversationState,
} from "./state";

type MessageWindowActions = Pick<
  ConversationState,
  | "setMessageWindow"
  | "prependMessages"
  | "appendNewerMessages"
  | "setLoadingOlder"
  | "setLoadingNewer"
  | "setMemoryUpdates"
  | "addMemoryUpdate"
  | "addMessage"
  | "insertMessageBefore"
  | "updateMessage"
  | "removeMessage"
  | "truncateAfter"
  | "reconcileLastTurn"
  | "setTurnSyncStatus"
  | "focusMessage"
  | "requestMessageFocus"
  | "clearPendingFocus"
>;

/** Message-window CRUD + focus / memory cards (non-fold mutations). */
export function createMessageWindowActions(
  set: ConversationSet,
  get: ConversationGet,
): MessageWindowActions {
  const patchConversation = createPatchConversation(set);

  return {
    setMessageWindow: (messages, flags, conversationId) => {
      const state = get();
      const key = conversationId ?? state.currentConversationId ?? DRAFT_KEY;
      if (
        !isMessageWindowResident(
          state.currentConversationId,
          state.byId,
          conversationId,
        )
      ) {
        logEvent("info", "conversation.slice_diag", {
          action: "reject_not_resident",
          conversation_id: key,
          active_id: state.currentConversationId,
          incoming_count: messages.length,
          has_more_after: flags.hasMoreAfter,
          has_more_before: flags.hasMoreBefore,
        });
        return;
      }
      patchConversation(conversationId, (rt) => ({
        messages,
        hasMoreBefore: flags.hasMoreBefore,
        hasMoreAfter: flags.hasMoreAfter,
        // Settled transcript replaces the live gauge. A turn still in flight
        // keeps the gauge so a window refresh cannot rewind it to an older receipt.
        ...(rt.isGenerating ? {} : { ceoWindowTokens: null }),
      }));
    },

    prependMessages: (older, hasMoreBefore, conversationId) =>
      patchConversation(conversationId, (rt) => {
        if (older.length === 0) return { hasMoreBefore };
        const known = new Set(rt.messages.map((m) => m.id));
        const fresh = older.filter((m) => !known.has(m.id));
        return { messages: [...fresh, ...rt.messages], hasMoreBefore };
      }),

    appendNewerMessages: (newer, hasMoreAfter, conversationId) =>
      patchConversation(conversationId, (rt) => {
        if (newer.length === 0) return { hasMoreAfter };
        const known = new Set(rt.messages.map((m) => m.id));
        const fresh = newer.filter((m) => !known.has(m.id));
        return { messages: [...rt.messages, ...fresh], hasMoreAfter };
      }),

    setLoadingOlder: (v, conversationId) =>
      patchConversation(conversationId, () => ({ loadingOlder: v })),

    setLoadingNewer: (v, conversationId) =>
      patchConversation(conversationId, () => ({ loadingNewer: v })),

    // 记忆更新对话内可见 (§1.6): replace the tail cards — only the latest-window
    // loads call this (a jump/around window has no tail), so it never clobbers cards
    // while the user is reading mid-history.
    setMemoryUpdates: (updates, conversationId) =>
      patchConversation(conversationId, () => ({ memoryUpdates: updates })),

    // Live firehose append (`memory_updated`): dedup by id, and ONLY when the slice is
    // already loaded — never materialise an empty runtime for a background conversation
    // (it would have a card but no messages); that conversation fetches the card itself
    // on next open. Appended last because consolidation post-dates every message.
    addMemoryUpdate: (update, conversationId) =>
      set((state) => {
        const key = conversationId ?? state.currentConversationId ?? DRAFT_KEY;
        const cur = state.byId[key];
        if (!cur) return {};
        if (cur.memoryUpdates.some((u) => u.id === update.id)) return {};
        return {
          byId: {
            ...state.byId,
            [key]: { ...cur, memoryUpdates: [...cur.memoryUpdates, update] },
          },
        };
      }),

    addMessage: (message, conversationId) =>
      patchConversation(conversationId, (rt) => ({
        messages: [...rt.messages, message],
      })),

    insertMessageBefore: (message, beforeId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        if (
          rt.messages.some(
            (m) => m.id === message.id || m.serverMessageId === message.id,
          )
        ) {
          return null;
        }
        let idx = rt.messages.findIndex(
          (m) => m.id === beforeId || m.serverMessageId === beforeId,
        );
        if (idx < 0) {
          for (let i = rt.messages.length - 1; i >= 0; i--) {
            if (rt.messages[i]?.role === "assistant") {
              idx = i;
              break;
            }
          }
        }
        if (idx < 0) {
          return { messages: [...rt.messages, message] };
        }
        const messages = rt.messages.slice();
        messages.splice(idx, 0, message);
        return { messages };
      }),

    updateMessage: (id, update, conversationId) =>
      patchConversation(conversationId, (rt) => ({
        messages: rt.messages.map((m) =>
          m.id === id ? { ...m, ...update } : m,
        ),
      })),

    removeMessage: (id, conversationId) =>
      patchConversation(conversationId, (rt) => {
        if (!rt.messages.some((m) => m.id === id)) return null;
        return { messages: rt.messages.filter((m) => m.id !== id) };
      }),

    truncateAfter: (id, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const idx = rt.messages.findIndex((m) => m.id === id);
        if (idx === -1) return null;
        return {
          messages: rt.messages.slice(0, idx + 1),
          hasMoreAfter: false,
        };
      }),

    reconcileLastTurn: (userMessageId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === "user") {
            messages[i] = { ...messages[i], id: userMessageId };
            break;
          }
        }
        return { messages };
      }),

    setTurnSyncStatus: (userMessageId, syncStatus, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const userIdx = messages.findIndex(
          (m) => m.id === userMessageId && m.role === "user",
        );
        if (userIdx === -1) return null;
        messages[userIdx] = { ...messages[userIdx], syncStatus };
        for (let i = userIdx + 1; i < messages.length; i++) {
          if (messages[i].role === "assistant") {
            messages[i] = { ...messages[i], syncStatus };
            break;
          }
          if (messages[i].role === "user") break;
        }
        return { messages };
      }),

    focusMessage: (id, conversationId) =>
      patchConversation(conversationId, (rt) => ({
        messageFocus: { id, nonce: (rt.messageFocus?.nonce ?? 0) + 1 },
      })),

    requestMessageFocus: (conversationId, messageId) =>
      set({ pendingFocus: { conversationId, messageId } }),

    clearPendingFocus: () => set({ pendingFocus: null }),
  };
}
