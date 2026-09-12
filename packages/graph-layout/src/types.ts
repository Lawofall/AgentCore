/**
 * Layout-facing graph edge + algorithm kind — shared by ELK compute and promo
 * precompute. Kept free of UI-store / Zustand so promo can import by package name.
 */

/**
 * Structural edge. Visual state (animated) is derived live by the host.
 *
 * `dep` (default) is a DAG dependency / input·captain bookend flow; `delegate`
 * is a captain worker → its nested sub-worker (阶段2 父子分组); `continuation`
 * is an original worker → its「修订 vN」续写 child; `handoff` is 回落换人「接替」.
 */
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind?: "dep" | "delegate" | "continuation" | "inject" | "handoff";
}

/**
 * Collaboration-graph layout algorithm (user-switchable on desktop canvas).
 * `leftright` is the product default (widescreen); `tree` is the same layered
 * algorithm rotated top-down.
 */
export type GraphLayout = "tree" | "leftright";
