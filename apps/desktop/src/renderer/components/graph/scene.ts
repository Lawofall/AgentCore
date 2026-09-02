/**
 * GraphScene — the collaboration graph's structural intermediate representation.
 *
 * A single pure function ({@link buildGraphScene}) sits between the protocol-fold
 * {@link Execution} and the ReactFlow projection / ELK layout. It is the ONE
 * place the graph's structural rules are decided: unit fold, continuation-chain
 * attribution, sub-team compounds, act membership, debate-beat folding, and the
 * three band families (wave / delegate batch / act, plus debate stages).
 *
 * Downstream (ELK layout, ReactFlow projection, band rendering) consumes the
 * scene's conclusions — it must NOT re-derive them:
 *   - 「接续链归哪个子队盒」→ {@link GraphScene.nodeGroup} / {@link GraphScene.layoutHints}
 *   - 「辩论 beat 折进哪个宿主」→ {@link GraphScene.beatFoldsByHost}
 *   - 分带归属 → {@link GraphScene.bands} (structural membership; pixels via
 *     {@link projectSceneBands}, never node-AABB reverse-derivation)
 *
 * The `acts → units → members` tree is first-class so batch R2 (幕级 LOD) can
 * fold at the act level straight from the IR without touching positions.
 *
 * This layer lives strictly downstream of the protocol fold — it never mutates
 * {@link Execution} and is pure w.r.t. its inputs (no ELK / no positions), so a
 * golden snapshot can pin the structure.
 */

import { NODE_HEIGHT, NODE_WIDTH } from "@/lib/graphMetrics";
import { type LayoutHints, computeLayoutHints } from "@/lib/layoutHints";
import type {
  ActAuthorizedBy,
  ActKind,
  Execution,
  ExecutionStatus,
  RunNode,
} from "@/stores/execution";
import type { GraphEdge, GraphLayout } from "@/stores/graph";
import { formatActBandLabel } from "./actAuthLabels";
import {
  type GraphFoldInfo,
  type GraphRunLike,
  type SubTeam,
  buildGraphStructure,
  computeGraphFold,
  debateStatementHostId,
  isDebateClosingRun,
  isDebateFoldedBeatRun,
  isDebateParticipantRun,
  resolveCaptainSinkId,
  workerRunsOf,
} from "./helpers";
import { INPUT_ID } from "./ids";

const WAVE_PAD = 8;
const STAGE_PAD = 16;
const ACT_BAND_PAD = 20;

/** A background band rect in flow coordinates (rendered via ViewportPortal). */
export interface WaveBand {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  labelX: number;
  labelY: number;
}

// ── Structural IR types ─────────────────────────────────────────────────────

/** One layout unit (a top-level graph node) with the members folded inside it. */
export interface SceneUnit {
  /** Layout-unit root run id (worker root / debate moderator / chain head). */
  id: string;
  /** Act the unit belongs to (旧 run_plan → 合成 act-1). */
  actId: string;
  /** Debate moderator compound (always layout-expanded). */
  debate: boolean;
  /** Compound group id when the unit renders as a sub-team box; else null. */
  groupId: string | null;
  /** Folded descendant run ids that render inside the box (empty = bare node). */
  memberIds: string[];
}

/** One act in the execution's act sequence, carrying its top-level units.
 *
 * The derived fields (`status` … `pendingDecisions`) are pure functions of the
 * execution's runs, decided once here so batch R2 (幕级 LOD) can render a 幕摘要卡
 * and auto-focus the active act straight from the IR — the golden pins them, and
 * no host re-derives act status from laid-out nodes. Every field uses only data
 * really available on runs (无编造)。 */
export interface SceneAct {
  actId: string;
  kind: ActKind | null;
  title: string | null;
  authorizedBy: ActAuthorizedBy | null;
  anchorRunId: string | null;
  /** Top-level unit ids in this act, in graph order. */
  unitIds: string[];
  /** All member run ids in this act (excl. captain + folded debate beats). */
  memberRunIds: string[];
  /** Aggregate lifecycle status of the act (running > failed > cancelled > completed > planning). */
  status: ExecutionStatus;
  /** Distinct participant roles in the act, first-seen order — drives 幕摘要卡 avatars. */
  roles: string[];
  /** Distinct participating agents in the act. */
  agentCount: number;
  /** Completed member runs / total member runs (progress bar on the card). */
  completed: number;
  total: number;
  /** Summed member run durations, or null when none reported. */
  durationMs: number | null;
  /** Pending boss decisions in this act (pending escalations + pending checkpoints). */
  pendingDecisions: number;
}

export type SceneBandKind = "act" | "wave" | "delegate" | "debateStage";

/**
 * A structural band: which runs it spans + how it labels itself, decided once
 * here. Pixels are a downstream concern ({@link projectSceneBands}); membership
 * is never re-derived from laid-out node AABBs.
 */
export interface SceneBand {
  id: string;
  kind: SceneBandKind;
  label: string;
  /** Runs whose laid-out positions define the band extent (attribution-resolved). */
  memberRunIds: string[];
}

export interface GraphSceneBands {
  /** The WaveLanes layer: act bands (≥2 acts) OR wave/delegate bands, or []. */
  lanes: SceneBand[];
  /** The DebateStageBands layer (第 N 轮 / 结辩). */
  debateStages: SceneBand[];
}

export interface GraphScene {
  /** 幕 → 单元 → 成员（结构主轴；R2 幕级 LOD 的地基）。 */
  acts: SceneAct[];
  /** Flat list of all top-level units (across acts), in graph order. */
  units: SceneUnit[];
  /** Graph edges lifted to layout units (= 旧 rawEdges)。 */
  edges: GraphEdge[];
  /** 结构派生分带（非节点 AABB 反推）。 */
  bands: GraphSceneBands;
  /** ELK layout node ids (incl. input / captain bookends). */
  nodeIds: string[];
  /** Sub-team compounds (incl. nesting). */
  subTeams: SubTeam[];
  /** Fold info (folded / unitOf / debateUnits / descendants). */
  fold: GraphFoldInfo;
  /** run id → innermost compound groupId it renders in (null = top-level) —
   * the single「接续链归盒」conclusion the projection reads. */
  nodeGroup: Map<string, string | null>;
  /** 折进拍宿主 run id → 被折 beat run ids（质询/复攻/crux）——唯一结论。 */
  beatFoldsByHost: Map<string, string[]>;
  /** ELK compound-build hints (continuation descendants + edge ownership). */
  layoutHints: LayoutHints;
  /** CEO 汇聚点 run id（kind==="captain"）；无则 null。 */
  captainId: string | null;
  /** 幕级 LOD（批 R2）：进行中该自动聚焦哪一幕（末个有 running 成员的幕；否则
   * 推进最靠后、已开跑的幕；全未起 → 首幕；无幕 / 无 run → null）。UI 用作
   * 「执行中自动聚焦活跃幕」的默认，纯派生（进金样）。 */
  activeActId: string | null;
}

export interface BuildGraphSceneOptions {
  inputId?: string;
  expandedUnits?: ReadonlySet<string>;
}

// ── Wave scheduling (topological) ───────────────────────────────────────────

/** Kahn-style topological wave index per run (mirrors backend RunPlan.waves). */
export function computeTopologicalRunWaves(
  runs: GraphRunLike[],
  captainId: string | null,
): Map<string, number> {
  void captainId;
  const workerRuns = workerRunsOf(runs);
  if (workerRuns.length === 0) return new Map();

  const workerIds = new Set(workerRuns.map((r) => r.id));
  const runById = new Map(workerRuns.map((r) => [r.id, r]));
  const foldInfo = computeGraphFold(runs, captainId);

  const unitOf = (runId: string): string => foldInfo.unitOf.get(runId) ?? runId;

  const unitMembers = new Map<string, Set<string>>();
  for (const r of workerRuns) {
    const unitId = unitOf(r.id);
    const members = unitMembers.get(unitId) ?? new Set<string>();
    members.add(r.id);
    unitMembers.set(unitId, members);
  }

  const unitIds: string[] = [];
  const seenUnits = new Set<string>();
  for (const r of workerRuns) {
    const unitId = unitOf(r.id);
    if (seenUnits.has(unitId)) continue;
    seenUnits.add(unitId);
    unitIds.push(unitId);
  }

  const unitDeps = new Map<string, Set<string>>();
  for (const unitId of unitIds) {
    const deps = new Set<string>();
    const members = unitMembers.get(unitId);
    if (!members) continue;
    for (const memberId of members) {
      const r = runById.get(memberId);
      if (!r) continue;
      for (const depId of r.dependsOn ?? []) {
        if (!workerIds.has(depId)) continue;
        const depUnit = unitOf(depId);
        if (depUnit === unitId) continue;
        deps.add(depUnit);
      }
    }
    unitDeps.set(unitId, deps);
  }

  const waveByUnit = new Map<string, number>();
  const resolved = new Set<string>();
  let waveIndex = 0;
  let remaining = unitIds.filter((u) => !resolved.has(u));

  while (remaining.length > 0) {
    const wave = remaining.filter((u) => {
      const deps = unitDeps.get(u) ?? new Set<string>();
      return [...deps].every((d) => resolved.has(d));
    });
    if (wave.length === 0) {
      for (const u of remaining) waveByUnit.set(u, waveIndex);
      break;
    }
    for (const u of wave) {
      waveByUnit.set(u, waveIndex);
      resolved.add(u);
    }
    remaining = remaining.filter((u) => !resolved.has(u));
    waveIndex++;
  }

  const waveByRun = new Map<string, number>();
  for (const r of workerRuns) {
    waveByRun.set(r.id, waveByUnit.get(unitOf(r.id)) ?? 0);
  }
  return waveByRun;
}

/**
 * Top-level worker runs that participate in lane banding (exclude captain +
 * folded nested sub-workers — those live inside a sub-team box). Pure
 * continuations (`continuesRunId != null`) stay visible on the graph but do not
 * enter 委派/波次 strips — only cold-start units count. Structural: every such
 * unit is positioned by ELK, so it needs no position filter here.
 */
function laneEligibleRunIds(
  runs: GraphRunLike[],
  captainId: string | null,
  fold: GraphFoldInfo,
): string[] {
  void captainId;
  const out: string[] = [];
  for (const r of workerRunsOf(runs)) {
    if (r.continuesRunId != null) continue;
    if (fold.folded.has(r.id)) continue;
    if (fold.unitOf.get(r.id) !== r.id) continue;
    out.push(r.id);
  }
  return out;
}

// ── Band geometry (pure downstream projection) ──────────────────────────────

function bandFromMembers(
  id: string,
  label: string,
  members: { x: number; y: number }[],
  bbox: { width: number; height: number },
  /** When true, band spans the flow axis (topo wave column/row). When false,
   * band spans the cross axis (delegate-batch strip — each 委派 is a chain that
   * already advances along the flow, so we band the sibling stacks). */
  alongFlow: boolean,
  horizontal: boolean,
): WaveBand {
  if (alongFlow === horizontal) {
    // leftright + alongFlow → vertical strip; tree + !alongFlow → vertical strip
    const x0 = Math.min(...members.map((m) => m.x));
    const x1 = Math.max(...members.map((m) => m.x + NODE_WIDTH));
    return {
      id,
      label,
      x: x0 - WAVE_PAD,
      y: -WAVE_PAD,
      w: x1 - x0 + WAVE_PAD * 2,
      h: bbox.height + WAVE_PAD * 2,
      labelX: x0 - WAVE_PAD + 6,
      labelY: -WAVE_PAD + 6,
    };
  }
  const y0 = Math.min(...members.map((m) => m.y));
  const y1 = Math.max(...members.map((m) => m.y + NODE_HEIGHT));
  return {
    id,
    label,
    x: -WAVE_PAD,
    y: y0 - WAVE_PAD,
    w: bbox.width + WAVE_PAD * 2,
    h: y1 - y0 + WAVE_PAD * 2,
    labelX: -WAVE_PAD + 6,
    labelY: y0 - WAVE_PAD + 6,
  };
}

function aabbBand(
  id: string,
  label: string,
  pts: { x: number; y: number }[],
  pad: number,
  labelMode: "corner" | "center",
): WaveBand {
  const x0 = Math.min(...pts.map((p) => p.x));
  const y0 = Math.min(...pts.map((p) => p.y));
  const x1 = Math.max(...pts.map((p) => p.x + NODE_WIDTH));
  const y1 = Math.max(...pts.map((p) => p.y + NODE_HEIGHT));
  return {
    id,
    label,
    x: x0 - pad,
    y: y0 - pad,
    w: x1 - x0 + pad * 2,
    h: y1 - y0 + pad * 2,
    labelX: labelMode === "center" ? (x0 + x1) / 2 : x0 - pad + 8,
    labelY: labelMode === "center" ? y0 - pad : y0 - pad + 8,
  };
}

/**
 * Project structural {@link SceneBand}s to flow-coordinate rects using laid-out
 * positions. Preserves each band family's exact geometry (the only source of
 * band pixels), never re-deriving membership. Bands with no positioned member
 * are skipped (a collapsed / not-yet-laid-out band renders nothing).
 */
export function projectSceneBands(
  bands: readonly SceneBand[],
  positions: Record<string, { x: number; y: number }>,
  bbox: { width: number; height: number } | null,
  layoutKind: GraphLayout,
): WaveBand[] {
  const horizontal = layoutKind === "leftright";
  const hasBbox = !!bbox && bbox.width > 0 && bbox.height > 0;
  const out: WaveBand[] = [];
  for (const band of bands) {
    const pts = band.memberRunIds
      .map((id) => positions[id])
      .filter((p): p is { x: number; y: number } => p != null);
    if (pts.length === 0) continue;
    if (band.kind === "debateStage") {
      out.push(aabbBand(band.id, band.label, pts, STAGE_PAD, "center"));
      continue;
    }
    if (band.kind === "act") {
      if (hasBbox && bbox) {
        const b = bandFromMembers(
          band.id,
          band.label,
          pts,
          bbox,
          true,
          horizontal,
        );
        // 标签略内缩，与旧 AABB 标签锚点手感接近。
        out.push({ ...b, labelX: b.labelX + 2, labelY: b.labelY + 2 });
      } else {
        out.push(aabbBand(band.id, band.label, pts, ACT_BAND_PAD, "corner"));
      }
      continue;
    }
    // wave | delegate — strip geometry needs a bbox (parity: no bbox → skip).
    if (!hasBbox || !bbox) continue;
    out.push(
      bandFromMembers(
        band.id,
        band.label,
        pts,
        bbox,
        band.kind === "wave",
        horizontal,
      ),
    );
  }
  return out;
}

// ── Structural band membership ──────────────────────────────────────────────

/**
 * 幕分带（批 A3）：多幕图按 acts 分段，标签用 act.title（可带 authorized_by 角标）。
 * 单幕 / 无 acts → 空（保持既有单幕视觉，退给波次/委派带）。成员含全部本幕 run
 * （排除 captain 与折进拍），几何时按坐标裁掉未定位者。
 */
function buildActBands(
  execution: Execution,
  captainId: string | null,
): SceneBand[] {
  void captainId;
  const acts = execution.acts;
  if (!acts || acts.length < 2) return [];
  const bands: SceneBand[] = [];
  for (const act of acts) {
    const ids = workerRunsOf(execution.runs)
      .filter(
        (r) => (r.actId ?? "act-1") === act.actId && !isDebateFoldedBeatRun(r),
      )
      .map((r) => r.id);
    if (ids.length === 0) continue;
    bands.push({
      id: `act-band-${act.actId}`,
      kind: "act",
      label: formatActBandLabel(act.title, act.actId, act.authorizedBy),
      memberRunIds: ids,
    });
  }
  return bands;
}

/**
 * 波次 / 委派批分带。优先「第 N 次委派」（同回合合并 ≥2 批 run_plan 且顶层 worker
 * 可见时），否则退回 WaveScheduler 拓扑波次（批次 N）。多幕图由 {@link buildActBands}
 * 承接（本函数只在 <2 幕时调用）。
 */
function buildWaveOrDelegateBands(
  execution: Execution,
  fold: GraphFoldInfo,
  captainId: string | null,
): SceneBand[] {
  const runs = execution.runs;
  const byId = new Map(runs.map((r) => [r.id, r]));
  const laneIds = laneEligibleRunIds(runs, captainId, fold);

  const batchSet = new Set<number>();
  for (const id of laneIds) batchSet.add(byId.get(id)?.delegateBatch ?? 1);
  const delegateKeys = [...batchSet].sort((a, b) => a - b);

  if (delegateKeys.length >= 2) {
    const bands: SceneBand[] = [];
    for (let i = 0; i < delegateKeys.length; i++) {
      const batchKey = delegateKeys[i];
      const runIds = laneIds.filter(
        (id) => (byId.get(id)?.delegateBatch ?? 1) === batchKey,
      );
      if (runIds.length === 0) continue;
      bands.push({
        id: `delegate-${batchKey}`,
        kind: "delegate",
        label: `第 ${i + 1} 次委派`,
        memberRunIds: runIds,
      });
    }
    return bands.length >= 2 ? bands : [];
  }

  const waveByRun = computeTopologicalRunWaves(runs, captainId);
  const groups = new Map<number, string[]>();
  for (const id of laneIds) {
    const wave = waveByRun.get(id);
    if (wave === undefined) continue;
    const arr = groups.get(wave);
    if (arr) arr.push(id);
    else groups.set(wave, [id]);
  }
  const keys = [...groups.keys()].sort((a, b) => a - b);
  if (keys.length < 2) return [];
  return keys.map((waveKey, i) => {
    const runIds = groups.get(waveKey) as string[];
    return {
      id: `wave-${i}`,
      kind: "wave" as const,
      label: `批次 ${i + 1}`,
      memberRunIds: runIds,
    };
  });
}

/**
 * 辩论阶段（第 N 轮 / 结辩）：只含辩手可见列（陈词/结辩），质询折进轮节点不单独成段。
 * 主持人开场不参与（避免第 1 轮标签悬在主持与辩手之间）。
 */
function buildDebateStageBands(execution: Execution): SceneBand[] {
  const CLOSING_ORDER = Number.MAX_SAFE_INTEGER;
  const stages = new Map<number, { label: string; ids: string[] }>();
  const ensure = (order: number, label: string) => {
    let s = stages.get(order);
    if (!s) {
      s = { label, ids: [] };
      stages.set(order, s);
    }
    return s;
  };
  for (const r of execution.runs) {
    if (!isDebateParticipantRun(r)) continue;
    if (isDebateFoldedBeatRun(r)) continue;
    if (isDebateClosingRun(r)) {
      ensure(CLOSING_ORDER, "结辩").ids.push(r.id);
      continue;
    }
    const round =
      r.continuesRunId == null ? 1 : r.round && r.round > 1 ? r.round : 2;
    ensure(round, `第 ${round} 轮`).ids.push(r.id);
  }
  if (stages.size === 0) return [];
  const bands: SceneBand[] = [];
  for (const order of [...stages.keys()].sort((a, b) => a - b)) {
    const s = stages.get(order);
    if (!s) continue;
    bands.push({
      id: `debate-stage-${order}`,
      kind: "debateStage",
      label: s.label,
      memberRunIds: s.ids,
    });
  }
  return bands;
}

// ── The IR builder ──────────────────────────────────────────────────────────

/**
 * Build the {@link GraphScene} for one execution — the single structural source
 * for layout / projection / bands. Pure: no ELK, no positions.
 */
export function buildGraphScene(
  execution: Execution,
  opts: BuildGraphSceneOptions = {},
): GraphScene {
  const inputId = opts.inputId ?? INPUT_ID;
  const expandedUnits = opts.expandedUnits ?? new Set<string>();
  const runs = execution.runs;
  const captainId = resolveCaptainSinkId(runs);

  const {
    nodeIds,
    rawEdges: edges,
    subTeams,
    foldInfo,
  } = buildGraphStructure(runs as GraphRunLike[], inputId, expandedUnits);
  const layoutHints = computeLayoutHints(subTeams, edges);

  // 辩论 beat 折进宿主（唯一结论；下游投影据此取被折 run 数据）。
  const workerRuns = workerRunsOf(runs) as GraphRunLike[];
  const workerIds = new Set(workerRuns.map((r) => r.id));
  const beatFoldsByHost = new Map<string, string[]>();
  for (const r of workerRuns) {
    if (!isDebateFoldedBeatRun(r)) continue;
    const hostId = debateStatementHostId(r, workerRuns, workerIds);
    if (!hostId) continue;
    const arr = beatFoldsByHost.get(hostId) ?? [];
    arr.push(r.id);
    beatFoldsByHost.set(hostId, arr);
  }

  // 顶层布局单元（unitOf===self 且未折叠）→ 幕归属。
  const subTeamByParent = new Map(subTeams.map((st) => [st.parentId, st]));
  const units: SceneUnit[] = [];
  for (const r of workerRuns) {
    if (foldInfo.folded.has(r.id)) continue;
    if (foldInfo.unitOf.get(r.id) !== r.id) continue;
    const st = subTeamByParent.get(r.id);
    units.push({
      id: r.id,
      actId: r.actId ?? "act-1",
      debate: foldInfo.debateUnits.has(r.id),
      groupId: st?.groupId ?? null,
      memberIds: st ? [...st.memberIds] : [],
    });
  }

  const acts = buildActs(execution, units, captainId);

  const lanes =
    (execution.acts?.length ?? 0) >= 2
      ? buildActBands(execution, captainId)
      : buildWaveOrDelegateBands(execution, foldInfo, captainId);
  const debateStages = buildDebateStageBands(execution);

  return {
    acts,
    units,
    edges,
    bands: { lanes, debateStages },
    nodeIds,
    subTeams,
    fold: foldInfo,
    nodeGroup: layoutHints.nodeGroup,
    beatFoldsByHost,
    layoutHints,
    captainId,
    activeActId: computeActiveActId(acts),
  };
}

// ── Act-level derived state (批 R2 幕级 LOD, pure) ───────────────────────────

/** All display-visible member runs of an act (excl. captain + folded debate beats). */
function actMemberRuns(
  execution: Execution,
  actId: string,
  captainId: string | null,
): RunNode[] {
  void captainId;
  return workerRunsOf(execution.runs).filter(
    (r) =>
      (r.actId ?? "act-1") === actId &&
      !isDebateFoldedBeatRun(r as GraphRunLike),
  );
}

interface ActDerived {
  memberRunIds: string[];
  status: ExecutionStatus;
  roles: string[];
  agentCount: number;
  completed: number;
  total: number;
  durationMs: number | null;
  pendingDecisions: number;
}

/** Aggregate an act's runs into card-ready derived state — only real run data. */
function computeActDerived(members: RunNode[]): ActDerived {
  const roles: string[] = [];
  const seenRoles = new Set<string>();
  const seenAgents = new Set<string>();
  let completed = 0;
  let durationMs = 0;
  let anyDuration = false;
  let pendingDecisions = 0;
  for (const r of members) {
    const role = r.role ?? r.agentId ?? r.id;
    if (!seenRoles.has(role)) {
      seenRoles.add(role);
      roles.push(role);
    }
    if (r.agentId) seenAgents.add(r.agentId);
    if (r.status === "completed" || r.status === "skipped") completed++;
    if (r.durationMs != null && r.durationMs > 0) {
      durationMs += r.durationMs;
      anyDuration = true;
    }
    for (const e of r.escalations ?? []) {
      if (e.status === "pending") pendingDecisions++;
    }
    if (r.checkpoint?.status === "pending") pendingDecisions++;
  }
  const statuses = members.map((r) => r.status);
  let status: ExecutionStatus;
  if (statuses.some((s) => s === "running")) status = "running";
  else if (pendingDecisions > 0) status = "paused";
  else if (statuses.some((s) => s === "failed")) status = "failed";
  else if (statuses.some((s) => s === "cancelled")) status = "cancelled";
  else if (
    statuses.length > 0 &&
    statuses.every((s) => s === "completed" || s === "skipped")
  )
    status = "completed";
  else status = "planning";
  return {
    memberRunIds: members.map((r) => r.id),
    status,
    roles,
    agentCount: seenAgents.size,
    completed,
    total: members.length,
    durationMs: anyDuration ? durationMs : null,
    pendingDecisions,
  };
}

/**
 * 执行中该自动聚焦哪一幕：末个含 running/paused 成员的幕；否则末个已开跑（非
 * planning）的幕；全未起 → 首幕；无幕 → null。纯派生（进金样），UI 据此在进行中
 * 自动聚焦活跃幕。
 */
function computeActiveActId(acts: SceneAct[]): string | null {
  if (acts.length === 0) return null;
  for (let i = acts.length - 1; i >= 0; i--) {
    if (acts[i].status === "running" || acts[i].status === "paused")
      return acts[i].actId;
  }
  for (let i = acts.length - 1; i >= 0; i--) {
    if (acts[i].status !== "planning") return acts[i].actId;
  }
  return acts[0].actId;
}

function buildActs(
  execution: Execution,
  units: SceneUnit[],
  captainId: string | null,
): SceneAct[] {
  const decls = execution.acts;
  if (!decls || decls.length === 0) {
    const derived = computeActDerived(
      actMemberRuns(execution, "act-1", captainId),
    );
    return [
      {
        actId: "act-1",
        kind: null,
        title: null,
        authorizedBy: null,
        anchorRunId: null,
        unitIds: units.map((u) => u.id),
        ...derived,
      },
    ];
  }
  const byActId = new Map<string, SceneAct>();
  const acts: SceneAct[] = [];
  const make = (base: {
    actId: string;
    kind: ActKind | null;
    title: string | null;
    authorizedBy: ActAuthorizedBy | null;
    anchorRunId: string | null;
  }): SceneAct => {
    const derived = computeActDerived(
      actMemberRuns(execution, base.actId, captainId),
    );
    const sa: SceneAct = { ...base, unitIds: [], ...derived };
    byActId.set(base.actId, sa);
    acts.push(sa);
    return sa;
  };
  for (const a of decls) {
    make({
      actId: a.actId,
      kind: a.kind,
      title: a.title,
      authorizedBy: a.authorizedBy,
      anchorRunId: a.anchorRunId,
    });
  }
  for (const u of units) {
    const sa =
      byActId.get(u.actId) ??
      make({
        actId: u.actId,
        kind: null,
        title: null,
        authorizedBy: null,
        anchorRunId: null,
      });
    sa.unitIds.push(u.id);
  }
  return acts;
}

// ── Backward-compatible band wrappers (single source: scene + geometry) ──────
// These preserve the pre-R1 call sites (tests / product-manual embed). Live
// hosts (GraphView on InlineTeamGraph / TurnDetailPage) build the scene once and call
// {@link projectSceneBands} directly instead of rebuilding it here.

/**
 * Wave / batch lanes behind the collaboration graph. Multi-act graphs (acts≥2)
 * return [] here (bands come from {@link computeActBands}).
 */
export function computeWaves(
  execution: Execution,
  positions: Record<string, { x: number; y: number }>,
  bbox: { width: number; height: number },
  layoutKind: GraphLayout,
  _captainId: string | null,
): WaveBand[] {
  const scene = buildGraphScene(execution);
  const laneBands = scene.bands.lanes.filter(
    (b) => b.kind === "wave" || b.kind === "delegate",
  );
  return projectSceneBands(laneBands, positions, bbox, layoutKind);
}

/**
 * 幕分带（批 A3）。单幕 / 无 acts → []（退给 {@link computeWaves}）。
 */
export function computeActBands(
  execution: Execution,
  positions: Record<string, { x: number; y: number }>,
  _captainId: string | null,
  bbox?: { width: number; height: number } | null,
  layoutKind: GraphLayout = "leftright",
): WaveBand[] {
  const scene = buildGraphScene(execution);
  const actBands = scene.bands.lanes.filter((b) => b.kind === "act");
  return projectSceneBands(actBands, positions, bbox ?? null, layoutKind);
}

/**
 * 辩论阶段标签（协作图）。非辩论 / 无坐标 → []。
 */
export function computeDebateStageBands(
  execution: Execution,
  positions: Record<string, { x: number; y: number }>,
  _captainId: string | null,
): WaveBand[] {
  const scene = buildGraphScene(execution);
  return projectSceneBands(
    scene.bands.debateStages,
    positions,
    null,
    "leftright",
  );
}
