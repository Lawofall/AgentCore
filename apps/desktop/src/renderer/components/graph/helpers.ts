/** Pure graph derivation helpers (status, handoffs, artifacts, wave lanes). */

import type { DebateBeat, Execution, RunStatus } from "@/stores/execution";
import {
  debateBeatFromContext,
  isDebateFoldedBeat,
  isDebateStatementBeat,
  isDebateTaggedRun,
} from "@/stores/execution";
import type { GraphEdge } from "@/stores/graph";
import type { EdgeHandoff } from "./StepEdge";
import { subTeamGroupId } from "./ids";

const PRODUCING_TOOLS = new Set(["file_write", "file_append", "str_replace"]);

/** CEO bookend run (`run_started.kind=captain`) — never a graph worker. */
export function isCaptainKind(r: { kind?: string | null }): boolean {
  return r.kind === "captain";
}

/**
 * Sink id for「CEO 汇总」bookend. Plan order first captain (stable with historical
 * `find`). Cross-turn append may leave *extra* captains in `runs` — those must
 * not become workers /「CEO 子队」parents; only this sink is drawn.
 */
export function resolveCaptainSinkId(
  runs: ReadonlyArray<{ id: string; kind?: string | null }>,
): string | null {
  return runs.find((r) => r.kind === "captain")?.id ?? null;
}

/** Non-captain runs for layout / lanes / sub-teams / status aggregation. */
export function workerRunsOf<T extends { kind?: string | null }>(
  runs: ReadonlyArray<T>,
): T[] {
  return runs.filter((r) => r.kind !== "captain");
}

/** Incremental-kickoff / hard-stop: a worker is in flight. Captain is the CEO turn. */
export function hasActiveRunningWorkers(
  runs: ReadonlyArray<{ kind?: string | null; status: string }>,
): boolean {
  return workerRunsOf(runs).some((r) => r.status === "running");
}

const WORKER_TERMINAL = new Set<string>([
  "completed",
  "failed",
  "cancelled",
  "skipped",
]);

export function deriveCaptainStatus(
  execution: Execution,
  captainId: string,
  opts?: { turnTerminal?: boolean; detached?: boolean },
): RunStatus {
  if (execution.status === "cancelled") return "cancelled";
  // Cold pause (ask_user / plan_review / …): workers may all be done, but CEO
  // is waiting on the user — never paint the sink as「正在收尾」.
  // RunStatus has no `paused`; `pending` clears the synthesis spinner.
  if (execution.status === "paused") return "pending";
  // Captain paints failed only from its own run_failed — whole-graph
  // execution.failed (worker 429 / synthesis miss) must not redden「CEO 汇总」.
  const captainRun = execution.runs.find((r) => r.id === captainId);
  if (captainRun?.status === "failed") return "failed";
  if (captainRun?.status === "cancelled") return "cancelled";
  // Exclude *all* captains (not only `captainId`): append-turn captains must not
  // count as incomplete "workers" and stall the sink on pending.
  const workers = workerRunsOf(execution.runs);
  // 工人未齐 = 待汇总。CEO 已 end_turn（turnTerminal）或 fold 误标
  // execution.completed 都不能把节点焊成「已汇总」——图是进度真相。
  if (workers.some((r) => !WORKER_TERMINAL.has(r.status))) return "pending";
  if (execution.status === "completed") return "completed";
  // Workers all terminal but captain never failed: not synthesizing, not 失败.
  if (execution.status === "failed") return "pending";
  // Workers all terminal, execution still live: same-turn CEO writing the close.
  // Captain already left (detached) — no second close; don't paint「正在收尾」.
  if (workers.length > 0) {
    if (opts?.detached) return "pending";
    return "running";
  }
  // No workers: message_end still means this captain-only turn closed.
  if (opts?.turnTerminal) return "completed";
  return "pending";
}

/**
 * CEO 汇聚点下面那两行摘录。
 *
 * 待汇总（工人未齐）：派单正文不是成果，不摘「人已派出 / 还在等」。
 * 有中间草稿才显示草稿；等待条走 statusCaption（face 正文最多两行），
 * preview 留空以免两行打架。
 * 人齐之后仍用派单泡开头（图挂在派单泡上；后台散了不再另开收口泡）。
 */
export function captainSinkPreview(opts: {
  captainStatus: RunStatus;
  answerPreview?: string | null;
  synthesisPreview?: string | null;
  waitCaption?: string | null;
}): string {
  const wait = (opts.waitCaption ?? "").trim();
  const synth = (opts.synthesisPreview ?? "").trim();
  const answer = (opts.answerPreview ?? "").trim();
  if (opts.captainStatus === "pending") {
    return wait ? "" : synth;
  }
  if (answer) return answer;
  return wait ? "" : synth;
}

export function resolveHandoff(
  execution: Execution,
  source: string,
  target: string,
): EdgeHandoff | null {
  const targetRun = execution.runs.find((r) => r.id === target);
  if (!targetRun) return null;
  const block = targetRun.receivedContext.find(
    (b) => b.channel === "dependency" && b.source_run_id === source,
  );
  if (!block) return null;
  return {
    fidelity: block.fidelity,
    truncated: block.truncated,
    sourceRole: block.source_role,
    chars: block.chars,
  };
}

export function deriveArtifacts(
  toolCalls: {
    toolName: string;
    arguments: Record<string, unknown>;
    status: string;
  }[],
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const tc of toolCalls) {
    if (tc.status !== "success") continue;
    if (!PRODUCING_TOOLS.has(tc.toolName)) continue;
    const path = tc.arguments.path;
    if (typeof path !== "string" || path.length === 0) continue;
    if (seen.has(path)) continue;
    seen.add(path);
    out.push(path);
  }
  return out;
}

function isContinuationRun(r: GraphRunLike): boolean {
  return r.continuesRunId != null;
}

function _isSubRun(r: GraphRunLike, workerIds: Set<string>): boolean {
  return (
    !isContinuationRun(r) &&
    !!r.parentRunId &&
    r.parentRunId !== r.id &&
    workerIds.has(r.parentRunId)
  );
}

export interface GraphRunLike {
  id: string;
  dependsOn: string[];
  parentRunId?: string | null;
  continuationIndex?: number;
  continuesRunId?: string | null;
  replacesRunId?: string | null;
  stance?: string | null;
  group?: string | null;
  round?: number;
  kind?: string;
  /** 幕归属（批 A3）；缺省按 act-1，跨幕 parent 不折进子队盒。 */
  actId?: string;
  /** 同回合第几次 delegate 追加（呈现层；测试可省略 → 视为单批）。 */
  delegateBatch?: number;
  /** 辩论 continue_run 的 `run_context`（读 channel 得 beat）；测试可省略。 */
  receivedContext?: ReadonlyArray<{ channel: string }> | null;
  status?: RunStatus;
  durationMs?: number | null;
}

function actIdOf(r: GraphRunLike | undefined | null): string {
  return r?.actId ?? "act-1";
}

/** Display-only signal that a run is a debate participant (辩手 / 续轮 revision / 证人席). */
export function isDebateParticipantRun(r: GraphRunLike): boolean {
  return isDebateTaggedRun(r);
}

/** 批 D1 · 辩论幕内证人席位（非答问续写）：`group=debate:witness` 且无 continues。
 * Only needs the two display fields, so node presentation can pass a projected
 * subset (not a full {@link GraphRunLike}). */
export function isWitnessSeatRun(
  r: Pick<GraphRunLike, "group" | "continuesRunId">,
): boolean {
  return r.group === "debate:witness" && r.continuesRunId == null;
}

/** 协作图可见列用的 beat：质询折进同轮陈词，结辩仍独立。与 arena 分桶共用 {@link debateBeatFromContext}。 */
export function graphDebateBeat(r: GraphRunLike): DebateBeat {
  return debateBeatFromContext(r.receivedContext);
}

/** 陈词宿主 / 发言列：与 {@link isDebateStatementBeat} 同口径。 */
export function isDebateStatementRun(r: GraphRunLike): boolean {
  return isDebateParticipantRun(r) && isDebateStatementBeat(r.receivedContext);
}

/** 质询作答：同辩手同轮的 continue_run，协作图不独立成列。 */
export function isDebateCrossExamRun(r: GraphRunLike): boolean {
  return isDebateParticipantRun(r) && graphDebateBeat(r) === "cross_exam";
}

/** 折进同轮宿主的 beat（质询 / 复攻 / crux）。 */
export function isDebateFoldedBeatRun(r: GraphRunLike): boolean {
  return isDebateParticipantRun(r) && isDebateFoldedBeat(r.receivedContext);
}

/** 结辩：独立列 +「结辩」角标。 */
export function isDebateClosingRun(r: GraphRunLike): boolean {
  return isDebateParticipantRun(r) && graphDebateBeat(r) === "closing";
}

/**
 * 同辩手同轮的可见宿主：质询 / 复攻 / crux 折进此节点。
 * 首轮与续轮陈词 / 攻击 / 回应 / 线程均算宿主。
 */
export function debateStatementHostId(
  folded: GraphRunLike,
  runs: GraphRunLike[],
  workerIds?: Set<string>,
): string | null {
  if (!isDebateFoldedBeatRun(folded)) return null;
  const ids = workerIds ?? new Set(runs.map((r) => r.id));
  const byId = new Map(runs.map((r) => [r.id, r]));
  const root = continuationRootId(folded.id, byId, ids);
  const round = folded.round ?? 0;
  for (const r of runs) {
    if (!ids.has(r.id)) continue;
    if (continuationRootId(r.id, byId, ids) !== root) continue;
    if ((r.round ?? 0) !== round) continue;
    if (isDebateFoldedBeatRun(r) || isDebateClosingRun(r)) continue;
    if (!isDebateStatementRun(r) && !isDebateParticipantRun(r)) continue;
    // 优先同轮可见宿主（statement/attack/defense/thread）。
    if (isDebateStatementRun(r)) return r.id;
  }
  // 回退：同根同轮任一非折叠、非结辩参与者。
  for (const r of runs) {
    if (!ids.has(r.id)) continue;
    if (continuationRootId(r.id, byId, ids) !== root) continue;
    if ((r.round ?? 0) !== round) continue;
    if (isDebateFoldedBeatRun(r) || isDebateClosingRun(r)) continue;
    return r.id;
  }
  return null;
}

/** 运行中 / 失败优先于完成（轮节点聚合质询后的可见状态）。 */
export function aggregateDebateRoundStatus(
  statuses: readonly RunStatus[],
): RunStatus {
  if (statuses.length === 0) return "pending";
  if (statuses.some((s) => s === "failed")) return "failed";
  if (statuses.some((s) => s === "running")) return "running";
  if (statuses.some((s) => s === "cancelled")) return "cancelled";
  if (statuses.every((s) => s === "completed")) return "completed";
  if (statuses.every((s) => s === "skipped")) return "skipped";
  return "pending";
}

/** 轮内活跃 beat：折进拍在跑 / 待答 → cross_exam（统称交锋拍）；否则立论。 */
export function debateRoundActiveBeat(
  statementStatus: RunStatus,
  cxStatuses: readonly RunStatus[],
): "statement" | "cross_exam" {
  if (cxStatuses.some((s) => s === "running")) {
    return "cross_exam";
  }
  if (
    (statementStatus === "completed" || statementStatus === "cancelled") &&
    cxStatuses.some((s) => s === "pending")
  ) {
    return "cross_exam";
  }
  return "statement";
}

/** 直播态轮节点状态条文案后缀（立论中 / 交锋作答中）。 */
export function debateRoundPhaseLabel(
  aggregated: RunStatus,
  activeBeat: "statement" | "cross_exam",
  hasCrossExam: boolean,
): string | null {
  if (!hasCrossExam || aggregated !== "running") return null;
  // 正反质询与红队复攻 / 圆桌 crux 共用此后缀（图上不区分拍种细文案）。
  return activeBeat === "cross_exam" ? "质询作答中" : "立论中";
}

/**
 * 收场态轮节点折进拍标记：正反「含质询」/ 红队「含复攻」/ 圆桌「含 crux」；
 * 失败时整行归因。立论失败（折进拍未败）不挂标记，沿用默认「失败」。
 */
export type DebateCrossExamMark = {
  label: string;
  mode: "suffix" | "replace";
};

export function debateRoundSettledMark(
  aggregated: RunStatus,
  hasFolded: boolean,
  cxStatuses: readonly RunStatus[],
  foldedBeats: readonly DebateBeat[] = [],
): DebateCrossExamMark | null {
  if (!hasFolded) return null;
  const kinds = new Set(foldedBeats);
  const onlyRebuttal =
    kinds.size > 0 && [...kinds].every((b) => b === "rebuttal");
  const onlyCrux = kinds.size > 0 && [...kinds].every((b) => b === "crux");
  const failLabel = onlyRebuttal
    ? "复攻失败"
    : onlyCrux
      ? "crux 失败"
      : "质询作答失败";
  const okLabel = onlyRebuttal ? "含复攻" : onlyCrux ? "含 crux" : "含质询";
  if (aggregated === "failed" && cxStatuses.some((s) => s === "failed")) {
    return { label: failLabel, mode: "replace" };
  }
  if (aggregated === "completed") {
    return { label: okLabel, mode: "suffix" };
  }
  return null;
}

/**
 * 质询直达 runId：活跃 running > 失败 > 最新。
 * 与直播 faceRun / activateId 选取同构，供收场「含质询」点击入口。
 */
export function pickDebateCrossExamActivateId(
  cxRuns: ReadonlyArray<{ id: string; status: RunStatus }>,
): string | null {
  if (cxRuns.length === 0) return null;
  const active = cxRuns.find((r) => r.status === "running");
  if (active) return active.id;
  for (let i = cxRuns.length - 1; i >= 0; i--) {
    if (cxRuns[i].status === "failed") return cxRuns[i].id;
  }
  return cxRuns[cxRuns.length - 1].id;
}

function continuationRootId(
  runId: string,
  runById: Map<string, GraphRunLike>,
  workerIds: Set<string>,
): string {
  let cur = runId;
  const seen = new Set<string>();
  while (!seen.has(cur)) {
    seen.add(cur);
    const r = runById.get(cur);
    if (!r?.continuesRunId || !workerIds.has(r.continuesRunId)) break;
    cur = r.continuesRunId;
  }
  return cur;
}

/** Moderator run id inferred from debate participant parent chain. */
export function debateModeratorId(
  runs: GraphRunLike[],
  captainId: string | null,
): string | null {
  void captainId;
  const workers = workerRunsOf(runs);
  const workerIds = new Set(workers.map((r) => r.id));
  const runById = new Map(workers.map((r) => [r.id, r]));
  for (const r of workers) {
    if (!isDebateParticipantRun(r)) continue;
    const root = continuationRootId(r.id, runById, workerIds);
    const rootRun = runById.get(root);
    const parentId = rootRun?.parentRunId;
    if (parentId && workerIds.has(parentId)) return parentId;
  }
  return null;
}

function belongsToDebateUnit(
  r: GraphRunLike,
  moderatorId: string,
  runById: Map<string, GraphRunLike>,
  workerIds: Set<string>,
): boolean {
  if (r.id === moderatorId) return false;
  if (isDebateParticipantRun(r)) return true;
  if (r.continuesRunId && workerIds.has(r.continuesRunId)) {
    const root = runById.get(continuationRootId(r.id, runById, workerIds));
    return root != null && isDebateParticipantRun(root);
  }
  return false;
}

/** Run-level fold: which runs collapse under a parent unit on the collaboration graph. */
export interface GraphFoldInfo {
  /** Run ids hidden from the top-level graph (children of a layout unit). */
  folded: Set<string>;
  /** Every worker run id → its visible layout-unit root id. */
  unitOf: Map<string, string>;
  /** Debate moderator unit roots — always layout-expanded (参与者×轮次 grid). */
  debateUnits: Set<string>;
  /** Layout unit id → all folded descendant run ids (for drill-in / subTeams). */
  descendants: Map<string, string[]>;
}

export function computeGraphFold(
  runs: GraphRunLike[],
  captainId: string | null,
): GraphFoldInfo {
  void captainId;
  const workers = workerRunsOf(runs);
  const workerIds = new Set(workers.map((r) => r.id));
  const runById = new Map(workers.map((r) => [r.id, r]));
  // 多主持人（多辩论幕）时按辩手 parent 链收集全部 moderator unit。
  const debateUnits = new Set<string>();
  for (const r of workers) {
    if (!isDebateParticipantRun(r)) continue;
    const root = continuationRootId(r.id, runById, workerIds);
    const rootRun = runById.get(root);
    const parentId = rootRun?.parentRunId;
    if (parentId && workerIds.has(parentId)) debateUnits.add(parentId);
  }
  const unitOf = new Map<string, string>();

  const resolveUnit = (runId: string): string => {
    const cached = unitOf.get(runId);
    if (cached) return cached;

    const r = runById.get(runId);
    if (!r) {
      unitOf.set(runId, runId);
      return runId;
    }

    // 辩论主持人永远是独立 layout unit——A2 把 parent_run_id 挂到宿主汇总员，
    // 不得因此折进汇总员子队盒。
    if (debateUnits.has(r.id)) {
      unitOf.set(runId, runId);
      return runId;
    }

    for (const modId of debateUnits) {
      if (belongsToDebateUnit(r, modId, runById, workerIds)) {
        unitOf.set(runId, modId);
        return modId;
      }
    }

    if (r.continuesRunId && workerIds.has(r.continuesRunId)) {
      for (const modId of debateUnits) {
        if (belongsToDebateUnit(r, modId, runById, workerIds)) {
          unitOf.set(runId, modId);
          return modId;
        }
      }
      // Non-debate continuations stay individually visible (continuation chain on the graph).
      unitOf.set(runId, runId);
      return runId;
    }

    if (
      !r.continuesRunId &&
      r.parentRunId &&
      r.parentRunId !== r.id &&
      workerIds.has(r.parentRunId)
    ) {
      const parent = runById.get(r.parentRunId);
      // 跨幕 parent（幕间衔接）不折进父幕子队——各自成 unit，边另画。
      if (actIdOf(r) !== actIdOf(parent)) {
        unitOf.set(runId, runId);
        return runId;
      }
      const u = resolveUnit(r.parentRunId);
      unitOf.set(runId, u);
      return u;
    }

    unitOf.set(runId, runId);
    return runId;
  };

  for (const r of workers) resolveUnit(r.id);

  const folded = new Set<string>();
  for (const r of workers) {
    if (unitOf.get(r.id) !== r.id) folded.add(r.id);
  }

  const descendants = new Map<string, string[]>();
  for (const r of workers) {
    const unit = unitOf.get(r.id) ?? r.id;
    if (unit === r.id) continue;
    const arr = descendants.get(unit) ?? [];
    arr.push(r.id);
    descendants.set(unit, arr);
  }

  return { folded, unitOf, debateUnits, descendants };
}

/** Lift an edge endpoint to its layout unit (dedupe after lifting). */
function liftEdgeEndpoints(
  source: string,
  target: string,
  unitOf: Map<string, string>,
): { source: string; target: string } | null {
  const src = unitOf.get(source) ?? source;
  const tgt = unitOf.get(target) ?? target;
  if (src === tgt) return null;
  return { source: src, target: tgt };
}

export interface SubTeam {
  parentId: string;
  memberIds: string[];
  groupId: string;
}

/** Build ELK node ids + graph edges from projected runs (plan + continuations). */
export function buildGraphStructure(
  runs: GraphRunLike[],
  inputId: string,
  expandedUnits: ReadonlySet<string> = new Set(),
): {
  nodeIds: string[];
  rawEdges: GraphEdge[];
  subTeams: SubTeam[];
  foldInfo: GraphFoldInfo;
} {
  const captainId = resolveCaptainSinkId(runs);
  // Drop *every* captain from workers — append turns add fresh captain runs that
  // must not render as AgentNode「CEO / 排队中」or spawn「CEO 子队」boxes.
  const workerRuns = workerRunsOf(runs);
  const workerIds = new Set(workerRuns.map((r) => r.id));
  const foldInfo = computeGraphFold(runs, captainId);
  const { folded, unitOf, descendants, debateUnits } = foldInfo;
  const runById = new Map(workerRuns.map((r) => [r.id, r]));
  const isContinuation = (r: GraphRunLike): boolean => r.continuesRunId != null;
  /** 同幕 parent 才进子队盒；跨幕 parent 是幕间衔接边，不折盒。 */
  const isSub = (r: GraphRunLike): boolean => {
    if (isContinuation(r)) return false;
    if (
      !r.parentRunId ||
      r.parentRunId === r.id ||
      !workerIds.has(r.parentRunId)
    )
      return false;
    if (debateUnits.has(r.id)) return false;
    const parent = runById.get(r.parentRunId);
    return actIdOf(r) === actIdOf(parent);
  };
  const isCrossActParent = (r: GraphRunLike): boolean => {
    if (
      !r.parentRunId ||
      r.parentRunId === r.id ||
      !workerIds.has(r.parentRunId)
    )
      return false;
    const parent = runById.get(r.parentRunId);
    return actIdOf(r) !== actIdOf(parent);
  };

  /** 质询/复攻/crux 不进协作图列（折进同轮宿主）；结辩仍可见。 */
  const beatHidden = new Set(
    workerRuns.filter(isDebateFoldedBeatRun).map((r) => r.id),
  );

  /** Debate units are always expanded; other units follow user toggle. */
  const isUnitExpanded = (unit: string): boolean =>
    debateUnits.has(unit) || expandedUnits.has(unit);

  const isLayoutVisible = (runId: string): boolean => {
    if (beatHidden.has(runId)) return false;
    if (!folded.has(runId)) return true;
    const unit = unitOf.get(runId) ?? runId;
    return isUnitExpanded(unit);
  };

  const layoutWorkers = workerRuns.filter((r) => isLayoutVisible(r.id));
  const nodeIds = layoutWorkers.map((s) => s.id);
  const debate = workerRuns.some((r) => r.stance != null);
  if (debate) {
    const rank = (id: string) => {
      const st = workerRuns.find((r) => r.id === id)?.stance;
      return st === "pro" ? 0 : st === "con" ? 2 : 1;
    };
    nodeIds.sort((a, b) => rank(a) - rank(b));
  }

  const edgeKey = (e: Pick<GraphEdge, "source" | "target" | "kind">) =>
    `${e.kind ?? "dep"}:${e.source}->${e.target}`;
  const edgeSet = new Map<string, GraphEdge>();

  const addEdge = (e: GraphEdge, lift = false) => {
    const src = lift ? (unitOf.get(e.source) ?? e.source) : e.source;
    const tgt = lift ? (unitOf.get(e.target) ?? e.target) : e.target;
    if (src === tgt) return;
    if (beatHidden.has(src) || beatHidden.has(tgt)) return;
    if (!isLayoutVisible(src) && folded.has(src)) return;
    if (!isLayoutVisible(tgt) && folded.has(tgt)) return;
    const lifted = lift ? liftEdgeEndpoints(e.source, e.target, unitOf) : null;
    const finalSrc = lifted?.source ?? src;
    const finalTgt = lifted?.target ?? tgt;
    if (finalSrc === finalTgt) return;
    if (beatHidden.has(finalSrc) || beatHidden.has(finalTgt)) return;
    const key = edgeKey({ ...e, source: finalSrc, target: finalTgt });
    if (edgeSet.has(key)) return;
    edgeSet.set(key, { ...e, id: e.id, source: finalSrc, target: finalTgt });
  };

  for (const run of workerRuns) {
    if (beatHidden.has(run.id)) continue;
    for (const depId of run.dependsOn) {
      const collapsed =
        folded.has(run.id) && !isUnitExpanded(unitOf.get(run.id) ?? run.id);
      addEdge(
        {
          id: `${depId}->${run.id}`,
          source: depId,
          target: run.id,
          kind: "dep",
        },
        collapsed,
      );
    }
  }

  const subTeamMap = new Map<string, string[]>();
  for (const r of layoutWorkers) {
    if (!isSub(r)) continue;
    const parentId = r.parentRunId as string;
    if (!isLayoutVisible(parentId)) continue;
    // 辩论 compound 内禁止再嵌套子队盒：旧 journal 庭前附属 run parent=主辩时，若仍按 isSub 挂到主辩，
    // 会拆出「主辩盒 × 方」假分带（与主持人 compound 叠成 3 盒 / 大片空白）。
    // 边仍要画进 compound，只是不另开 parent 子队。
    const parentUnit = unitOf.get(parentId) ?? parentId;
    if (debateUnits.has(parentUnit) && parentId !== parentUnit) {
      addEdge({
        id: `${parentId}=>${r.id}`,
        source: parentId,
        target: r.id,
        kind: "delegate",
      });
      continue;
    }
    const arr = subTeamMap.get(parentId) ?? [];
    arr.push(r.id);
    subTeamMap.set(parentId, arr);
    addEdge({
      id: `${parentId}=>${r.id}`,
      source: parentId,
      target: r.id,
      kind: "delegate",
    });
  }

  // 幕间衔接边：跨幕 parent_run_id（汇总员 → 辩论主持人），不进子队盒。
  for (const r of layoutWorkers) {
    if (!isCrossActParent(r) || !r.parentRunId) continue;
    if (!isLayoutVisible(r.parentRunId) || !isLayoutVisible(r.id)) continue;
    addEdge({
      id: `${r.parentRunId}->act->${r.id}`,
      source: r.parentRunId,
      target: r.id,
      kind: "dep",
    });
  }

  // Debate units are always expanded: one flat sub-team holds visible debate
  // descendants (辩手 + 轮次陈词 + 结辩；质询已折进轮节点) so ELK lays 参与者×轮次.
  for (const modId of debateUnits) {
    const members = (descendants.get(modId) ?? []).filter(
      (id) => id !== modId && !beatHidden.has(id),
    );
    if (members.length > 0) {
      subTeamMap.set(modId, members);
    }
  }

  const subTeams: SubTeam[] = [...subTeamMap.entries()].map(
    ([parentId, memberIds]) => ({
      parentId,
      memberIds,
      groupId: subTeamGroupId(parentId),
    }),
  );

  // 接续链只连可见节点（轮→轮→结辩），跳过已折进的质询，避免悬空边 / phantom 列。
  // 星型 continuesRunId 铺成链（历史教训：勿照星型画平行边）。
  const continuationsByOriginal = new Map<string, GraphRunLike[]>();
  for (const r of layoutWorkers) {
    if (
      isContinuation(r) &&
      r.continuesRunId &&
      workerIds.has(r.continuesRunId)
    ) {
      const list = continuationsByOriginal.get(r.continuesRunId) ?? [];
      list.push(r);
      continuationsByOriginal.set(r.continuesRunId, list);
    }
  }
  // 有 continuation 后继的节点：不画 unit→CEO bookend（仅链尖汇入）。
  const hasContinuationSuccessor = new Set<string>();
  for (const [originalId, continuations] of continuationsByOriginal) {
    if (beatHidden.has(originalId) || !isLayoutVisible(originalId)) continue;
    const ordered = continuations
      .slice()
      .sort((a, b) => (a.continuationIndex ?? 0) - (b.continuationIndex ?? 0));
    let prev = originalId;
    for (const cont of ordered) {
      addEdge({
        id: `${prev}~>${cont.id}`,
        source: prev,
        target: cont.id,
        kind: "continuation",
      });
      hasContinuationSuccessor.add(prev);
      prev = cont.id;
    }
  }

  // 回落换人：replaces_run_id → new worker「接替」边（与接续链正交）。
  for (const r of layoutWorkers) {
    const from = r.replacesRunId;
    if (!from || !workerIds.has(from) || !isLayoutVisible(r.id)) continue;
    addEdge({
      id: `${from}=>handoff=>${r.id}`,
      source: from,
      target: r.id,
      kind: "handoff",
    });
  }

  const topWorkers = workerRuns.filter(
    (r) => unitOf.get(r.id) === r.id && !folded.has(r.id),
  );
  if (topWorkers.length > 0 && captainId) {
    // Units that another top-level worker depends on (i.e. have a downstream
    // peer). Leaves = not in this set → bookend edge into the CEO sink.
    const dependedOn = new Set<string>();
    for (const r of topWorkers) {
      for (const dep of r.dependsOn) dependedOn.add(unitOf.get(dep) ?? dep);
    }
    // 补派/接手：被 replaces_run_id 指向的失败节点不再作 CEO 汇入；补派节点本身
    // 也不是从用户输入扇出的新根（depends_on=[] 时勿画 input→补派）。
    const replacedUnits = new Set<string>();
    for (const r of topWorkers) {
      const from = r.replacesRunId;
      if (!from) continue;
      replacedUnits.add(unitOf.get(from) ?? from);
    }
    nodeIds.push(inputId, captainId);
    for (const r of topWorkers) {
      const unit = unitOf.get(r.id) ?? r.id;
      // 跨幕入口（辩论主持人 parent=汇总员）从锚点生长，不从「你的任务」扇出。
      // 续节点（continuesRunId）挂原节点后方成链，不接 input——只冷开局根扇出。
      if (
        r.dependsOn.length === 0 &&
        !r.replacesRunId &&
        !isCrossActParent(r) &&
        !isContinuation(r)
      ) {
        addEdge({
          id: `${inputId}->${unit}`,
          source: inputId,
          target: unit,
          kind: "dep",
        });
      }
      // 有 continuation 后继的节点（含冷开局根）不进 CEO；仅链尖 → captain。
      // 真实 dependsOn / 补派 replaced 仍按原规则跳过。
      if (
        !dependedOn.has(unit) &&
        !replacedUnits.has(unit) &&
        !hasContinuationSuccessor.has(r.id) &&
        !hasContinuationSuccessor.has(unit)
      ) {
        addEdge({
          id: `${unit}->${captainId}`,
          source: unit,
          target: captainId,
          kind: "dep",
        });
      }
    }
  }

  return {
    nodeIds,
    rawEdges: [...edgeSet.values()],
    subTeams,
    foldInfo,
  };
}
