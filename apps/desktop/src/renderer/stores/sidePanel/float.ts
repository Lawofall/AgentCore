import { canRevealSidePanel, persistOpen } from "./chrome";
import {
  floatingIdSet,
  homeTabAfterDetailClose,
  maxFloatZ,
  withFloatFocused,
} from "./helpers";
import {
  CHANGES_TAB_ID,
  DEFAULT_FLOAT_HEIGHT,
  DEFAULT_FLOAT_WIDTH,
  type DetailTab,
  FLOAT_CASCADE_OFFSET,
  type FloatLayout,
  type FloatableTabKind,
  MAX_FLOATS,
  type SidePanelFloat,
  type SidePanelGet,
  type SidePanelSet,
  type SidePanelState,
  WORKSPACE_TAB_ID,
} from "./types";

export function isFloatableKind(
  kind: DetailTab["kind"] | "workspace" | "changes",
): kind is FloatableTabKind {
  return (
    kind === "run" ||
    kind === "file" ||
    kind === "workspace" ||
    kind === "changes"
  );
}

/** Whether this strip / fixed id may float under §十. */
export function canFloatTabId(
  tabId: string,
  tabs: readonly DetailTab[],
): boolean {
  if (tabId === WORKSPACE_TAB_ID || tabId === CHANGES_TAB_ID) return true;
  const tab = tabs.find((t) => t.id === tabId);
  return tab != null && isFloatableKind(tab.kind);
}

export function defaultFloatLayout(
  existingCount: number,
  maxZ: number,
): FloatLayout {
  const offset = existingCount * FLOAT_CASCADE_OFFSET;
  return {
    x: 72 + offset,
    y: 72 + offset,
    width: DEFAULT_FLOAT_WIDTH,
    height: DEFAULT_FLOAT_HEIGHT,
    zIndex: maxZ + 1,
  };
}

export { maxFloatZ, withFloatFocused };

/** After Move'ing `floatedId` out of the dock, pick a remaining dock active id. */
export function nextDockActiveAfterFloat(
  state: {
    activeTabId: string;
    tabs: readonly DetailTab[];
    floats: readonly SidePanelFloat[];
    changesOpen: boolean;
  },
  floatedId: string,
): string {
  if (state.activeTabId !== floatedId) return state.activeTabId;
  const floating = floatingIdSet(state.floats);
  floating.add(floatedId);
  const dockedContent = state.tabs.filter((t) => !floating.has(t.id));
  if (dockedContent.length > 0) {
    return dockedContent[dockedContent.length - 1]?.id ?? WORKSPACE_TAB_ID;
  }
  // 工作区仍停靠时回工作区。「改动」仅 changesOpen 时作 float 后默认 home。
  if (!floating.has(WORKSPACE_TAB_ID)) return WORKSPACE_TAB_ID;
  if (state.changesOpen && !floating.has(CHANGES_TAB_ID)) {
    return CHANGES_TAB_ID;
  }
  return WORKSPACE_TAB_ID;
}

type FloatActions = Pick<
  SidePanelState,
  | "isFloating"
  | "floatTab"
  | "dockTab"
  | "destroyFloat"
  | "clearFloats"
  | "setFloatLayout"
  | "focusFloat"
  | "focusDock"
>;

/** In-app float Move semantics (Move, not Copy). */
export function createFloatActions(
  set: SidePanelSet,
  get: SidePanelGet,
): FloatActions {
  return {
    isFloating: (tabId) => get().floats.some((f) => f.tabId === tabId),

    floatTab: (tabId, layout) => {
      const state = get();
      if (tabId === CHANGES_TAB_ID && !state.changesOpen) return false;
      if (!canFloatTabId(tabId, state.tabs)) return false;
      const existing = state.floats.find((f) => f.tabId === tabId);
      if (existing) {
        get().focusFloat(tabId);
        return true;
      }
      if (state.floats.length >= MAX_FLOATS) return false;

      const nextLayout: FloatLayout = {
        ...defaultFloatLayout(state.floats.length, maxFloatZ(state.floats)),
        ...layout,
        zIndex: maxFloatZ(state.floats) + 1,
      };
      const activeTabId = nextDockActiveAfterFloat(state, tabId);
      set({
        floats: [...state.floats, { tabId, layout: nextLayout }],
        activeTabId,
        focusSurface: { type: "float", tabId },
      });
      return true;
    },

    dockTab: (tabId) => {
      const state = get();
      if (!state.floats.some((f) => f.tabId === tabId)) return;
      if (!canRevealSidePanel()) return;
      persistOpen(true);
      set((s) => ({
        floats: s.floats.filter((f) => f.tabId !== tabId),
        open: true,
        pendingBadge: 0,
        activeTabId: tabId,
        focusSurface: { type: "dock" as const },
      }));
    },

    destroyFloat: (tabId) => {
      if (tabId === WORKSPACE_TAB_ID) return false;
      if (tabId === CHANGES_TAB_ID) {
        if (!get().floats.some((f) => f.tabId === CHANGES_TAB_ID)) return false;
        get().closeChanges();
        return true;
      }
      const state = get();
      if (!state.floats.some((f) => f.tabId === tabId)) return false;
      const tab = state.tabs.find((t) => t.id === tabId);
      if (!tab || (tab.kind !== "run" && tab.kind !== "file")) return false;
      // closeTab also strips the float entry.
      get().closeTab(tabId);
      return true;
    },

    clearFloats: () => {
      set((s) => {
        const floatingIds = floatingIdSet(s.floats);
        if (floatingIds.size === 0) {
          return s.focusSurface.type === "float"
            ? { focusSurface: { type: "dock" as const } }
            : s;
        }
        const tabs = s.tabs.filter((t) => !floatingIds.has(t.id));
        let activeTabId = s.activeTabId;
        const activeGone =
          floatingIds.has(activeTabId) ||
          (activeTabId !== WORKSPACE_TAB_ID &&
            activeTabId !== CHANGES_TAB_ID &&
            !tabs.some((t) => t.id === activeTabId));
        if (activeGone) {
          activeTabId = tabs[tabs.length - 1]?.id ?? homeTabAfterDetailClose();
        }
        return {
          floats: [],
          tabs,
          activeTabId,
          focusSurface: { type: "dock" as const },
        };
      });
    },

    setFloatLayout: (tabId, layout) => {
      set((s) => {
        if (!s.floats.some((f) => f.tabId === tabId)) return s;
        return {
          floats: s.floats.map((f) =>
            f.tabId === tabId
              ? { ...f, layout: { ...f.layout, ...layout } }
              : f,
          ),
        };
      });
    },

    focusFloat: (tabId) => {
      set((s) => {
        if (!s.floats.some((f) => f.tabId === tabId)) return s;
        // Already the focus surface → no-op. Re-bumping zIndex here fed the
        // OS dual-float focus ping-pong (open-all → focus → focusFloat → …).
        if (s.focusSurface.type === "float" && s.focusSurface.tabId === tabId) {
          return s;
        }
        return {
          floats: withFloatFocused(s.floats, tabId),
          focusSurface: { type: "float" as const, tabId },
        };
      });
    },

    focusDock: () => set({ focusSurface: { type: "dock" } }),
  };
}
