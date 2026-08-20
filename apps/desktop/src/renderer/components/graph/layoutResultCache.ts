/**
 * Module-level LRU for ELK layout results.
 *
 * Survives hook remount (session switch → back) so identical graph structure
 * does not re-run ELK. Cap + LRU eviction keep renderer heap bounded.
 */

import type { GroupLayout, NodeSizeMap } from "@/lib/elk-layout";
import type { GraphEdge, GraphLayout } from "@/stores/graph";
import type { ActCardLayout } from "./actLod";
import type { GraphFitMode } from "./useGraphViewport";

/** Enough for ~5 warm conversations × a few expanded turns; not unbounded. */
export const GRAPH_LAYOUT_CACHE_LIMIT = 16;

export interface CachedLayoutResult {
  positions: Record<string, { x: number; y: number }>;
  edges: GraphEdge[];
  width: number;
  height: number;
  nodeSizes: NodeSizeMap;
  groups: GroupLayout[];
  actCards: ActCardLayout[];
}

/** structuralKey (or turn structural fragment) + layoutKind + fitMode — all affect spacing/algorithm. */
export function layoutCacheKey(
  structuralKey: string,
  layoutKind: GraphLayout,
  fitMode: GraphFitMode,
): string {
  return `${structuralKey}::kind=${layoutKind}::fit=${fitMode}`;
}

const cache = new Map<string, CachedLayoutResult>();

export function getCachedLayout(key: string): CachedLayoutResult | undefined {
  const hit = cache.get(key);
  if (!hit) return undefined;
  // Refresh MRU order (Map insertion order).
  cache.delete(key);
  cache.set(key, hit);
  return hit;
}

export function setCachedLayout(key: string, value: CachedLayoutResult): void {
  if (cache.has(key)) cache.delete(key);
  cache.set(key, value);
  while (cache.size > GRAPH_LAYOUT_CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

/** Test / probe helper — not used on the production hot path. */
export function clearLayoutResultCache(): void {
  cache.clear();
}

export function layoutResultCacheSize(): number {
  return cache.size;
}
