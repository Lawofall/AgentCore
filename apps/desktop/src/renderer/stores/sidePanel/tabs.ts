import { detachLocalBrowserHost } from "@/lib/detachLocalBrowserHost";
import { useConversationStore } from "../conversation";
import { canRevealSidePanel, persistOpen } from "./chrome";
import {
  capTabsProtectingFloats,
  floatingIdSet,
  homeTabAfterDetailClose,
  withFloatFocused,
} from "./helpers";
import {
  CHANGES_TAB_ID,
  type DetailTab,
  type SidePanelGet,
  type SidePanelSet,
  type SidePanelState,
  WORKSPACE_TAB_ID,
  browserDismissKey,
  terminalDismissKey,
} from "./types";

/** Kinds unloaded on conversation switch (定案 D); terminal / browser shells stay. */
const CONVERSATION_SCOPED_KINDS = new Set<DetailTab["kind"]>([
  "run",
  "content",
  "simple-turn",
  "file",
]);

function isConversationScopedKind(kind: DetailTab["kind"]): boolean {
  return CONVERSATION_SCOPED_KINDS.has(kind);
}

type TabsActions = Pick<
  SidePanelState,
  | "openTab"
  | "closeTab"
  | "reorderContentTabs"
  | "setActiveTab"
  | "closeContentTabs"
  | "closeConversationScopedTabs"
>;

/** Content-tab model: open / close / reorder / cap (floats protected). */
export function createTabsActions(
  set: SidePanelSet,
  get: SidePanelGet,
): TabsActions {
  return {
    openTab: (tab, opts) => {
      const reveal = opts?.reveal !== false && canRevealSidePanel();
      const activate = opts?.activate !== false;
      const state = get();
      const alreadyFloating = state.floats.some((f) => f.tabId === tab.id);

      // Move semantics: re-open of a floating tab updates in place + focuses float;
      // do not force the dock open or create a second visible copy.
      if (alreadyFloating) {
        set((s) => ({
          tabs: s.tabs.map((t) => (t.id === tab.id ? tab : t)),
          floats: activate ? withFloatFocused(s.floats, tab.id) : s.floats,
          ...(activate
            ? { focusSurface: { type: "float" as const, tabId: tab.id } }
            : {}),
        }));
        return;
      }

      if (reveal) persistOpen(true);
      set((s) => {
        const floatingIds = floatingIdSet(s.floats);
        const exists = s.tabs.some((t) => t.id === tab.id);
        // A re-open replaces the tab wholesale (same id ⇒ same kind, namespaced
        // prefixes guarantee it), refreshing its title/scope without merging kinds.
        let tabs = exists
          ? s.tabs.map((t) => (t.id === tab.id ? tab : t))
          : [...s.tabs, tab];
        // Cap closable content tabs: drop oldest *docked*; never evict floating.
        tabs = capTabsProtectingFloats(tabs, floatingIds);
        return {
          tabs,
          ...(reveal ? { open: true as const, pendingBadge: 0 } : {}),
          ...(activate
            ? {
                activeTabId: tab.id,
                focusSurface: { type: "dock" as const },
              }
            : {}),
        };
      });
    },

    closeTab: (id) => {
      const closing = get().tabs.find((t) => t.id === id);
      if (closing?.kind === "browser") {
        // 关浏览器 tab = 脱离保活（改 React 状态前显式 hide）。
        void detachLocalBrowserHost();
        const conversationId =
          useConversationStore.getState().currentConversationId;
        get().dismissAutoSurface(browserDismissKey(conversationId));
      }
      if (closing?.kind === "terminal") {
        const conversationId =
          useConversationStore.getState().currentConversationId;
        get().dismissAutoSurface(terminalDismissKey(conversationId));
      }
      set((s) => {
        const idx = s.tabs.findIndex((t) => t.id === id);
        const tabs = s.tabs.filter((t) => t.id !== id);
        const floats = s.floats.filter((f) => f.tabId !== id);
        let activeTabId = s.activeTabId;
        if (s.activeTabId === id) {
          // Fall back to the neighbour detail tab (next, else previous), else home.
          // Prefer a still-docked neighbour when floats remain in the strip.
          const floatingIds = floatingIdSet(floats);
          const dockedNeighbour =
            tabs.slice(idx).find((t) => !floatingIds.has(t.id)) ??
            [...tabs.slice(0, idx)]
              .reverse()
              .find((t) => !floatingIds.has(t.id)) ??
            null;
          const next = dockedNeighbour ?? tabs[idx] ?? tabs[idx - 1] ?? null;
          activeTabId = next ? next.id : homeTabAfterDetailClose();
        }
        let focusSurface = s.focusSurface;
        if (focusSurface.type === "float" && focusSurface.tabId === id) {
          focusSurface = { type: "dock" };
        }
        return { tabs, floats, activeTabId, focusSurface };
      });
    },

    reorderContentTabs: (orderedIds) => {
      set((s) => {
        if (orderedIds.length === 0) return s;
        const unique = new Set(orderedIds);
        if (unique.size !== orderedIds.length) return s;
        const byId = new Map(s.tabs.map((t) => [t.id, t]));
        if (orderedIds.some((id) => !byId.has(id))) return s;
        const matchCount = s.tabs.filter((t) => unique.has(t.id)).length;
        if (orderedIds.length !== matchCount) return s;

        const indices: number[] = [];
        for (let i = 0; i < s.tabs.length; i++) {
          const tab = s.tabs[i];
          if (tab && unique.has(tab.id)) indices.push(i);
        }
        const reordered = orderedIds.flatMap((id) => {
          const tab = byId.get(id);
          return tab ? [tab] : [];
        });
        if (reordered.length !== indices.length) return s;
        const tabs = [...s.tabs];
        for (let i = 0; i < indices.length; i++) {
          const at = indices[i];
          const next = reordered[i];
          if (at == null || next == null) return s;
          tabs[at] = next;
        }
        return { tabs };
      });
    },

    setActiveTab: (id) => {
      if (get().isFloating(id)) {
        get().focusFloat(id);
        return;
      }
      set({ activeTabId: id, focusSurface: { type: "dock" } });
    },

    closeContentTabs: () => {
      set((s) => {
        const droppedIds = new Set(
          s.tabs
            .filter((t) => t.kind === "content" || t.kind === "simple-turn")
            .map((t) => t.id),
        );
        const tabs = s.tabs.filter(
          (t) => t.kind !== "content" && t.kind !== "simple-turn",
        );
        if (tabs.length === s.tabs.length) return s;
        const floats = s.floats.filter((f) => !droppedIds.has(f.tabId));
        // If the dropped tab was active, fall back to a surviving detail tab (e.g. a
        // run drilled in the canvas, kept per §十) else the 工作区 home.
        const activeStillThere = tabs.some((t) => t.id === s.activeTabId);
        const activeTabId = activeStillThere
          ? s.activeTabId
          : (tabs[tabs.length - 1]?.id ?? homeTabAfterDetailClose());
        let focusSurface = s.focusSurface;
        if (
          focusSurface.type === "float" &&
          droppedIds.has(focusSurface.tabId)
        ) {
          focusSurface = { type: "dock" };
        }
        return { tabs, floats, activeTabId, focusSurface };
      });
    },

    closeConversationScopedTabs: () => {
      set((s) => {
        const droppedIds = new Set(
          s.tabs
            .filter((t) => isConversationScopedKind(t.kind))
            .map((t) => t.id),
        );
        if (droppedIds.size === 0) return s;
        const tabs = s.tabs.filter((t) => !isConversationScopedKind(t.kind));
        // Strip float entries for unloaded tabs only; workspace/changes floats
        // stay until clearFloats (same 切对话 effect can call both).
        const floats = s.floats.filter((f) => !droppedIds.has(f.tabId));
        const activeStillThere =
          s.activeTabId === WORKSPACE_TAB_ID ||
          s.activeTabId === CHANGES_TAB_ID ||
          tabs.some((t) => t.id === s.activeTabId);
        const activeTabId = activeStillThere
          ? s.activeTabId
          : (tabs[tabs.length - 1]?.id ?? homeTabAfterDetailClose());
        let focusSurface = s.focusSurface;
        if (
          focusSurface.type === "float" &&
          droppedIds.has(focusSurface.tabId)
        ) {
          focusSurface = { type: "dock" };
        }
        return { tabs, floats, activeTabId, focusSurface };
      });
    },
  };
}
