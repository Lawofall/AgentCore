import type { SidePanelGet, SidePanelSet, SidePanelState } from "./types";

type AutoSurfaceActions = Pick<
  SidePanelState,
  | "dismissAutoSurface"
  | "isAutoSurfaceDismissed"
  | "clearAutoSurfaceDismiss"
  | "incrementPendingBadge"
>;

/** Auto-surface dismiss memory + pending badge. */
export function createAutoSurfaceActions(
  set: SidePanelSet,
  get: SidePanelGet,
): AutoSurfaceActions {
  return {
    dismissAutoSurface: (contextId) => {
      set((s) => {
        const dismissedContexts = new Set(s.dismissedContexts);
        dismissedContexts.add(contextId);
        return { dismissedContexts };
      });
    },

    isAutoSurfaceDismissed: (contextId) =>
      get().dismissedContexts.has(contextId),

    clearAutoSurfaceDismiss: (contextId) => {
      set((s) => {
        if (!s.dismissedContexts.has(contextId)) return s;
        const dismissedContexts = new Set(s.dismissedContexts);
        dismissedContexts.delete(contextId);
        return { dismissedContexts };
      });
    },

    incrementPendingBadge: () =>
      set((s) => ({ pendingBadge: s.pendingBadge + 1 })),
  };
}
