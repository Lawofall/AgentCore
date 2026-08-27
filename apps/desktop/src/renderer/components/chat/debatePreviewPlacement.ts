/** Gate input: captain is the CEO bookend, not a worker. Missing kind = worker. */
export type TeamGraphRun = {
  status: string;
  kind?: string | null;
};

function isWorkerRun(run: TeamGraphRun): boolean {
  return run.kind !== "captain";
}

/**
 * True once a **worker** left the never-started states (`pending`, or terminal
 * `skipped` from finalize before a start). Captain `run_started` is the CEO
 * turn itself (often emitted before `run_plan` / kickoff) and must not count —
 * live SSE drops that frame; journal hydrate restores it.
 * Hang / stop-before-start stay graph-less unless workers are already running.
 * plan_review mid-wave pause (completed worker nodes exist) still shows the graph.
 */
export function teamHasStartedRuns(runs: readonly TeamGraphRun[]): boolean {
  return runs.some(
    (r) => isWorkerRun(r) && r.status !== "pending" && r.status !== "skipped",
  );
}

/**
 * Inline graph visibility. Graph is not gated on leftover kickoff IX;
 */
export function shouldShowTeamGraph(
  runs: readonly TeamGraphRun[] | null | undefined,
): boolean {
  const list = runs ?? [];
  if (teamHasStartedRuns(list)) return true;
  return list.some((r) => isWorkerRun(r) && r.status === "pending");
}

/** Alias kept for graph consumers / fixture tests. */
export function teamGraphVisible(
  runs: readonly TeamGraphRun[] | null | undefined,
): boolean {
  return shouldShowTeamGraph(runs);
}
