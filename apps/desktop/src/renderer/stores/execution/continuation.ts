import type { Execution, RunNode } from "./types";

/** Minimal run shape for walking `continuesRunId` to the chain root. */
export interface ContinuationLink {
  id: string;
  continuesRunId: string | null;
}

/**
 * Walk `continuesRunId` back to the original run id for this continuation chain.
 * Standalone runs (no `continuesRunId`) return themselves. Missing / cyclic links
 * stop at the last reachable id. Wire is star-shaped (always points at the root);
 * the walk also tolerates a linear chain if present.
 */
export function continuationRootId(
  runId: string,
  runs: ReadonlyArray<ContinuationLink>,
): string {
  const byId = new Map(runs.map((r) => [r.id, r]));
  let cur = runId;
  const seen = new Set<string>();
  while (!seen.has(cur)) {
    seen.add(cur);
    const r = byId.get(cur);
    if (!r?.continuesRunId || !byId.has(r.continuesRunId)) break;
    cur = r.continuesRunId;
  }
  return cur;
}

/** One version in a continuation chain: the original is `version` 1, each
 * 续写 carries its own `version` (2, 3… = continuationIndex + 1). `run` is the
 * projected node for that version. */
export interface ContinuationVersion {
  version: number;
  run: RunNode;
}

/** A worker's full continuation chain: the original plus every 续写 of it, in
 * version order (v1 first). */
export interface ContinuationChain {
  originalId: string;
  versions: ContinuationVersion[];
}

/** Whether any worker in the turn was 同人接续 — gates the统一「对比」透镜 + graph
 * continuation styling (mirrors {@link isDebate} for debates). */
export function hasContinuations(execution: Execution): boolean {
  return execution.runs.some((r) => r.continuesRunId != null);
}

/**
 * Group the turn's runs into continuation chains, one per continued original
 * (in first-seen original order). Each chain is the original (v1) followed by its
 * 续写 versions in ascending continuationIndex — the projection the统一「对比」透镜
 * lays out side by side. Originals with no continuation are omitted; a stray
 * continuation whose original is absent is dropped.
 */
export function continuationChains(execution: Execution): ContinuationChain[] {
  const byRoot = new Map<string, RunNode[]>();
  for (const run of execution.runs) {
    if (run.continuesRunId == null) continue;
    const list = byRoot.get(run.continuesRunId) ?? [];
    list.push(run);
    byRoot.set(run.continuesRunId, list);
  }
  const chains: ContinuationChain[] = [];
  for (const run of execution.runs) {
    const continuations = byRoot.get(run.id);
    if (run.continuesRunId != null || !continuations) continue;
    const versions: ContinuationVersion[] = [
      { version: 1, run },
      ...continuations
        .slice()
        .sort((a, b) => a.continuationIndex - b.continuationIndex)
        .map((r) => ({
          version: r.continuationIndex + 1,
          run: r,
        })),
    ];
    chains.push({ originalId: run.id, versions });
  }
  return chains;
}
