import type { TeamPreviewDisplay } from "@/stores/conversation";

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
 * Hang / stop-before-start stay graph-less unless {@link shouldShowTeamGraph}
 * also sees a continue kickoff. team_preview `adjust` 是回灌 CEO、不开工.
 * plan_review mid-wave pause (completed worker nodes exist) still shows the graph.
 */
export function teamHasStartedRuns(runs: readonly TeamGraphRun[]): boolean {
  return runs.some(
    (r) => isWorkerRun(r) && r.status !== "pending" && r.status !== "skipped",
  );
}

/**
 * 开工卡「已授权开工」族：仅 `continue`。
 * `adjust` 按卡种区分——team_preview 上是不开工、回灌 CEO，不得当已开工；
 * plan_review 的 adjust 不走此闸（波间复核，不藏进协作图）。
 */
export function isKickoffGoDecision(
  decision: TeamPreviewDisplay["decision"] | string | null | undefined,
): boolean {
  return decision === "continue";
}

export function isKickoffReleased(
  preview: Pick<TeamPreviewDisplay, "status" | "decision"> | null | undefined,
): boolean {
  return (
    preview?.status === "resolved" && isKickoffGoDecision(preview.decision)
  );
}

/** Still waiting on 开做 / 开赛 — a newer pending card blocks leftover go decisions. */
export function isKickoffPending(
  preview: Pick<TeamPreviewDisplay, "status" | "decision"> | null | undefined,
): boolean {
  return preview?.status === "pending";
}

/**
 * Per-message kickoff: a leftover pending card on this bubble still blocks
 * leaked resolved continue from an earlier batch. No card → released
 * (backend `command=auto`; do not wait for 「授权并开工」).
 */
export function kickoffReleasedFromPreviews(
  previews: readonly Pick<TeamPreviewDisplay, "status" | "decision">[],
): boolean {
  if (previews.length === 0) return true;
  if (previews.some(isKickoffPending)) return false;
  return previews.some(isKickoffReleased);
}

/**
 * Chat / canvas / turn-detail share this — same gate as
 * {@link shouldHostPreviewInGraph} (禁止图+废卡双写).
 * - 工人已开跑：出图（无卡 / 存量 pending 卡都不挡）。
 * - 本泡有存量待确认开工卡、工人未跑：不出图（注意力归最小壳）。
 * - 已授权 continue：pending 编制也出图。
 * - 取消 / 调整回灌 / 超时 / 失效且卡还在：不出图。
 * - 无卡 + 编制仍有 pending 工人：出图（不靠「授权并开工」）。
 */
export function teamGraphVisible(
  runs: readonly TeamGraphRun[] | null | undefined,
  previews: readonly Pick<TeamPreviewDisplay, "status" | "decision">[],
): boolean {
  if (shouldShowTeamGraph(runs, kickoffReleasedFromPreviews(previews))) {
    return true;
  }
  if (previews.length > 0) return false;
  const list = runs ?? [];
  return list.some((r) => isWorkerRun(r) && r.status === "pending");
}

/**
 * Inline graph visibility.
 * - 存量开工卡挂起（未拍板）：false，即使 run_plan 已把节点铺成 pending。
 * - captain-only running（CEO 本轮已开、工人未跑）：false，与「零 worker」同。
 * - 无卡或已授权 continue 且编制已在：true（pending 节点也画，不必等第一人开跑）。
 * - 取消 / 调整回灌 / 超时 / 失效且从未开跑：false。
 * - 已有工人开跑（含 plan_review 波间）：true，不依赖开工卡。
 */
export function shouldShowTeamGraph(
  runs: readonly TeamGraphRun[] | null | undefined,
  kickoffReleased = false,
): boolean {
  const list = runs ?? [];
  if (teamHasStartedRuns(list)) return true;
  return kickoffReleased && list.length > 0;
}

/**
 * Shared visibility for resolved team_preview content (debate or delegate):
 * when true → 图已出现则不画废卡（hide standalone ResolvedTeamPreview）；
 * when false → keep the standalone card (pending, cancel, or no plan yet).
 *
 * 藏卡与出图同一套闸（{@link teamGraphVisible}）：图出则藏废卡，
 * 取消/未开跑则留卡。`bubblePreviews` 里只要有 pending，leftover go
 * 不得藏卡（否则同泡新卡未拍板时图也不出 → 空窗）。缺省只看本卡。
 */
export function shouldHostPreviewInGraph(
  preview: Pick<TeamPreviewDisplay, "status" | "decision"> | null | undefined,
  runs: readonly TeamGraphRun[] | null | undefined,
  bubblePreviews?: readonly Pick<TeamPreviewDisplay, "status" | "decision">[],
): boolean {
  if (!preview || preview.status !== "resolved" || !runs) return false;
  return teamGraphVisible(runs, bubblePreviews ?? [preview]);
}
