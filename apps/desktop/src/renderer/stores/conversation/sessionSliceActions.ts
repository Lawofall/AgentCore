import { detachLocalBrowserHost } from "@/lib/detachLocalBrowserHost";
import { logEvent } from "@/lib/log";
import { DRAFT_KEY, EMPTY_RUNTIME } from "./runtime";
import { isConversationSliceBusy, pruneConversationSlices } from "./sliceLru";
import type {
  ConversationGet,
  ConversationSet,
  ConversationState,
} from "./state";

type SessionSliceActions = Pick<
  ConversationState,
  | "setCurrentConversation"
  | "dropConversationRuntime"
  | "switchConversation"
  | "adoptDraftRuntime"
  | "releaseBackgroundSlice"
>;

/** Session switch + slice LRU / explicit idle drop. */
export function createSessionSliceActions(
  set: ConversationSet,
  get: ConversationGet,
): SessionSliceActions {
  return {
    setCurrentConversation: (id) => set({ currentConversationId: id }),

    dropConversationRuntime: (id) =>
      set((state) => {
        const byId = { ...state.byId };
        delete byId[id];
        return {
          currentConversationId:
            state.currentConversationId === id
              ? null
              : state.currentConversationId,
          byId,
          sliceLruOrder: (state.sliceLruOrder ?? []).filter((k) => k !== id),
        };
      }),

    switchConversation: (id) => {
      const prevKey = get().currentConversationId ?? DRAFT_KEY;
      const nextKey = id ?? DRAFT_KEY;
      if (prevKey === nextKey) {
        set({ currentConversationId: id });
        return;
      }
      // 切对话 / 回首页 = 必须脱离 Local 浏览器附着（改状态前）。
      void detachLocalBrowserHost();
      set((state) => {
        const byId = { ...state.byId };
        const incoming = byId[nextKey];
        // 无目的地打开必须落最新：清 LRU 残留 focus，勿动 pendingFocus。
        byId[nextKey] = incoming
          ? { ...incoming, messageFocus: null }
          : { ...EMPTY_RUNTIME };
        const pruned = pruneConversationSlices(
          byId,
          state.sliceLruOrder ?? [],
          nextKey,
          prevKey,
        );
        return {
          currentConversationId: id,
          byId: pruned.byId,
          sliceLruOrder: pruned.sliceLruOrder,
        };
      });
    },

    adoptDraftRuntime: (newId) => {
      const prevKey = get().currentConversationId ?? DRAFT_KEY;
      void detachLocalBrowserHost();
      set((state) => {
        const draft = state.byId[DRAFT_KEY] ?? EMPTY_RUNTIME;
        const byId = { ...state.byId };
        byId[newId] = { ...draft, messageFocus: null };
        byId[DRAFT_KEY] = { ...EMPTY_RUNTIME };
        const pruned = pruneConversationSlices(
          byId,
          state.sliceLruOrder ?? [],
          newId,
          prevKey,
        );
        return {
          currentConversationId: newId,
          byId: pruned.byId,
          sliceLruOrder: pruned.sliceLruOrder,
        };
      });
    },

    releaseBackgroundSlice: (conversationId) =>
      set((state) => {
        const activeKey = state.currentConversationId ?? DRAFT_KEY;
        if (conversationId === activeKey) {
          logEvent("info", "conversation.slice_diag", {
            action: "release_skip_active",
            conversation_id: conversationId,
            active_id: activeKey,
            message_count: state.byId[conversationId]?.messages.length ?? 0,
          });
          return {};
        }
        const slice = state.byId[conversationId];
        if (!slice) {
          logEvent("info", "conversation.slice_diag", {
            action: "release_skip_missing",
            conversation_id: conversationId,
            active_id: activeKey,
          });
          return {};
        }
        if (isConversationSliceBusy(conversationId, slice)) {
          logEvent("info", "conversation.slice_diag", {
            action: "release_skip_busy",
            conversation_id: conversationId,
            active_id: activeKey,
            message_count: slice.messages.length,
            is_generating: slice.isGenerating,
          });
          return {};
        }
        logEvent("warn", "conversation.slice_diag", {
          action: "release_drop",
          conversation_id: conversationId,
          active_id: activeKey,
          message_count: slice.messages.length,
          has_more_after: slice.hasMoreAfter,
          has_more_before: slice.hasMoreBefore,
        });
        const byId = { ...state.byId };
        delete byId[conversationId];
        return {
          byId,
          sliceLruOrder: (state.sliceLruOrder ?? []).filter(
            (k) => k !== conversationId,
          ),
        };
      }),
  };
}
