/**
 * Collaboration-graph Document layer — structure fingerprint & shell snapshot keys.
 *
 * Document changes only when topology / fold / act focus change. ELK coordinates
 * and streaming live fields are intentionally excluded from the fingerprint;
 * positions ride along in the shell snapshot when ELK finishes.
 */

import type { Execution } from "@/stores/execution";

/**
 * Structural fingerprint for ELK re-layout / Document topology.
 * Content/streaming fields are intentionally excluded.
 */
export function graphStructureKey(
  runs: ReadonlyArray<{
    id: string;
    dependsOn: readonly string[];
    parentRunId?: string | null;
    replacesRunId?: string | null;
  }>,
): string {
  return runs
    .map(
      (s) =>
        `${s.id}:${s.dependsOn.join(",")}:${s.parentRunId ?? ""}:${s.replacesRunId ?? ""}`,
    )
    .join("|");
}

/**
 * GraphView parent-tree epoch: topology + act list + execution lifecycle.
 * Streaming deltas (output length / process chrome) must NOT change this —
 * Live faces subscribe via per-run signatures in graphLive.
 */
export function graphViewExecutionEpoch(
  execution: Execution | null | undefined,
): string {
  if (!execution) return "";
  const struct = graphStructureKey(execution.runs);
  const actsKey = execution.acts?.map((a) => a.actId).join(",") ?? "";
  return `${struct}::acts=${actsKey}::status=${execution.status}`;
}

/** Structure fingerprint for RF Document shells (setNodes/setEdges gate). */
export function graphDocumentFingerprint(opts: {
  execution: Execution;
  expandedUnits: ReadonlySet<string>;
  focusedActId: string | null | undefined;
  handleDirection: "horizontal" | "vertical";
  edgePathType?: "smoothstep" | "bezier";
}): string {
  const struct = graphStructureKey(opts.execution.runs);
  const expandKey = [...opts.expandedUnits].sort().join(",");
  const actsKey = opts.execution.acts?.map((a) => a.actId).join(",") ?? "";
  const path = opts.edgePathType ?? "smoothstep";
  return `${struct}::${expandKey}::acts=${actsKey}::focus=${opts.focusedActId ?? ""}::dir=${opts.handleDirection}::path=${path}`;
}

/**
 * Shell snapshot key for positions / compound groups / act-card placements.
 * Changing this updates node positions without implying a topology change.
 * Measure is not Document — RF node heights must not appear here.
 */
export function graphShellSnapshotKey(opts: {
  positions: Record<string, { x: number; y: number }>;
  groups: ReadonlyArray<{
    groupId: string;
    x: number;
    y: number;
    width: number;
    height: number;
  }>;
  nodeSizes: Record<string, { width: number; height: number }>;
  actCards: ReadonlyArray<{
    id: string;
    actId: string;
    x: number;
    y: number;
    index: number;
  }>;
  bbox: { width: number; height: number } | null;
  edgeIds: readonly string[];
}): string {
  const pos = Object.keys(opts.positions)
    .sort()
    .map((id) => {
      const p = opts.positions[id];
      return `${id}:${p.x},${p.y}`;
    })
    .join("|");
  const groups = opts.groups
    .map((g) => `${g.groupId}:${g.x},${g.y},${g.width},${g.height}`)
    .join("|");
  const sizes = Object.keys(opts.nodeSizes)
    .sort()
    .map((id) => {
      const s = opts.nodeSizes[id];
      return `${id}:${s.width}x${s.height}`;
    })
    .join("|");
  const cards = opts.actCards
    .map((c) => `${c.id}:${c.actId}:${c.x},${c.y}:${c.index}`)
    .join("|");
  const bbox = opts.bbox ? `${opts.bbox.width}x${opts.bbox.height}` : "";
  const edges = opts.edgeIds.join(",");
  return `${pos}#${groups}#${sizes}#${cards}#${bbox}#${edges}`;
}
