/**
 * @agentcore/graph-layout — pure ELK layout for the collaboration graph.
 * Shared by desktop renderer and promo precompute scripts (no @/ aliases).
 */
export type {
  GraphEdge,
  GraphLayout,
} from "./types";
export {
  NODE_WIDTH,
  NODE_HEIGHT,
  LAYOUT_PADDING,
  buildNodeSizeMap,
  type NodeSizeEntry,
  type NodeSizeMap,
} from "./graphMetrics";
export {
  computeLayoutHints,
  type LayoutHintTeam,
  type LayoutHints,
} from "./layoutHints";
export {
  NODE_SPACING_EMBED,
  NODE_SPACING_COMFORT,
  nodeSpacingForFitMode,
  COMPOUND_LAYER_SPACING,
  computeLayout,
  EMBED_MIN_HEIGHT,
  EMBED_MAX_HEIGHT,
  EMBED_DEFAULT_COL_WIDTH,
  workerGraphShape,
  estimateBbox,
  fitWidthBox,
  type LayoutResult,
  type GroupLayout,
  type SubTeamInput,
  type LayoutBookends,
  type GraphShape,
  type FitWidthBox,
} from "./elk-layout";
