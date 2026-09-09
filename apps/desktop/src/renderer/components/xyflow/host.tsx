/**
 * Shared @xyflow/react host — StoreUpdater-safe defaults for every canvas.
 *
 * Contract:
 * 1. Wrap each ReactFlow with {@link XyflowHost} (own store; never share an ancestor).
 * 2. Never pass `fitView` / `fitViewOptions` as ReactFlow props.
 * 3. Camera only via `instance.fitView` / `setViewport`, keyed by
 *    {@link xyflowCameraKey} (structure bbox × column width) — not by `nodes[]`
 *    or a new `fit` object every render.
 * 4. `proOptions` must be {@link XYFLOW_PRO_OPTIONS} — never an inline object.
 * 5. Callbacks passed to ReactFlow must be referentially stable (read latest
 *    data from refs).
 *
 * Collaboration embed (inline mount / width-vs-view) → `graphHost.tsx`.
 */
import { ReactFlowProvider } from "@xyflow/react";
import type { ReactNode } from "react";

export const XYFLOW_PRO_OPTIONS = { hideAttribution: true } as const;

/** Padding for instance.fitView — keep in sync across hosts. */
export const XYFLOW_FIT_PADDING = 0.2;

export function XyflowHost({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

/**
 * Identity for a camera write. Empty string = not ready (skip setViewport / fit).
 */
export function xyflowCameraKey(
  bbox: { width: number; height: number } | null | undefined,
  colWidth = 0,
): string {
  if (!bbox || colWidth < 0) return "";
  const box = `${bbox.width.toFixed(2)}x${bbox.height.toFixed(2)}`;
  if (colWidth === 0) return box;
  return `${box}@${Math.round(colWidth)}`;
}
