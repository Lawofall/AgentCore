import { create } from "zustand";
import { createAutoSurfaceActions } from "./autoSurface";
import { createChromeActions, loadOpen, loadWidth } from "./chrome";
import { createFacadeActions } from "./facades";
import { createFloatActions } from "./float";
import { createTabsActions } from "./tabs";
import { type SidePanelState, WORKSPACE_TAB_ID } from "./types";

export const useSidePanelStore = create<SidePanelState>((set, get) => ({
  open: loadOpen(),
  width: loadWidth(),
  tabs: [],
  // Content tabs are session-level, so a fresh load always starts on the workspace
  // home rather than a dangling tab id.
  activeTabId: WORKSPACE_TAB_ID,
  floats: [],
  focusSurface: { type: "dock" },
  changesOpen: false,
  changesFocusMessageId: null,
  dismissedContexts: new Set(),
  pendingBadge: 0,

  ...createAutoSurfaceActions(set, get),
  ...createTabsActions(set, get),
  ...createFloatActions(set, get),
  ...createFacadeActions(set, get),
  ...createChromeActions(set, get),
}));

/**
 * Esc / Ctrl+J when a float owns focus: 钉回 that float (关浮窗钉回).
 * Returns true when handled so callers skip dock-close.
 */
export function dismissFocusedFloat(): boolean {
  const state = useSidePanelStore.getState();
  if (state.focusSurface.type !== "float") return false;
  state.dockTab(state.focusSurface.tabId);
  return true;
}
