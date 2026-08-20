/**
 * Unified conversation side panel (前端UX设计.md §十) — the chat's single
 * right-docked surface. Package split by responsibility axis; public API is
 * unchanged — import from `@/stores/sidePanel`.
 */

export {
  sidePanelMaxWidth,
  SIDE_PANEL_MIN_WIDTH,
  SIDE_PANEL_DEFAULT_WIDTH,
  SIDE_PANEL_MAX_TABS,
  SIDE_PANEL_MAX_FLOATS,
  WORKSPACE_TAB_ID,
  CHANGES_TAB_ID,
  TEAM_BROWSER_TAB_ID,
  TEAM_TERMINAL_TAB_ID,
  terminalDismissKey,
  browserDismissKey,
  runDetailTabId,
  contentDetailTabId,
  simpleTurnDetailTabId,
  fileTabId,
  sidePanelFocusTabId,
  type RunDetailTab,
  type EndpointKind,
  type ContentDetailTab,
  type SimpleTurnDetailTab,
  type TerminalDetailTab,
  type FileDetailTab,
  type BrowserDetailTab,
  type DetailTab,
  type FloatableTabKind,
  type FloatLayout,
  type SidePanelFloat,
  type SidePanelFocusSurface,
} from "./types";

export { canRevealSidePanel } from "./chrome";
export { isFloatableKind, canFloatTabId } from "./float";
export { useSidePanelStore, dismissFocusedFloat } from "./store";
