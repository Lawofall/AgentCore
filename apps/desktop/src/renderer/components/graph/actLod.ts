/**
 * 幕级 LOD 布局（批 R2）——多幕（`scene.acts.length >= 2`）回合的顶层编排。
 *
 * 顶层是一条「幕摘要卡链」，恰好一个聚焦幕展开完整 DAG（自相似于画布「恰好一个
 * 聚焦回合」LOD）。ELK **只**为聚焦幕算完整布局（{@link computeLayout} 跑聚焦幕子图，
 * 复用画布 per-turn 多布局的现成范式）；非聚焦幕渲染为一张幕摘要卡节点；跨幕边降级
 * 为幕块间的链边（挂卡）。
 *
 * 链的顺序摆放（沿流向 cursor 递进 + 跨轴居中）是「组合一张元布局」，与画布把多回合
 * 叠成 spine 同类——不是对 ELK 输出的手工坐标校正，故多幕路径未新增手工校正。单幕
 * 回合不走此路径（宿主按 `scene.acts.length < 2` 分流到既有 computeLayout，像素级零变化）。
 */

import type { Execution, ExecutionStatus } from "@/stores/execution";
import type { GraphEdge, GraphLayout } from "@/stores/graph";
import {
  type GroupLayout,
  NODE_HEIGHT,
  NODE_WIDTH,
  type NodeSizeMap,
  buildNodeSizeMap,
  computeLayout,
  computeLayoutHints,
  nodeSpacingForFitMode,
} from "@agentcore/graph-layout";
import { INPUT_ID, actCardId, actChainEdgeId, parseActCardId } from "./ids";
import type { GraphScene, SceneAct } from "./scene";
import type { GraphFitMode } from "./useGraphViewport";

// Re-export the act-card id helpers (single source in ./ids) so existing
// consumers / tests that import them from actLod keep working.
export { actCardId, parseActCardId };

/** Fixed footprint of a folded 幕摘要卡 in flow coordinates (matches ActSummaryNode). */
export const ACT_CARD_W = 282;
export const ACT_CARD_H = 150;
const ACT_CHAIN_GAP = 56;
const ACT_CHAIN_PAD = 24;

/** A folded-act card placed in the top-level chain. */
export interface ActCardLayout {
  /** Synthetic node id (`__act__<actId>`). */
  id: string;
  actId: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** 1-based act number for the「幕 N」eyebrow. */
  index: number;
}

export interface ActLodLayout {
  /** Positions for the focused act's laid-out runs (offset into the chain) +
   * the act cards keyed by {@link actCardId}. Non-focused act runs have NO
   * position, so the run projection skips them and only the cards render. */
  positions: Record<string, { x: number; y: number }>;
  /** Focused act intra edges (offset-agnostic ids) + downgraded act-chain edges. */
  edges: GraphEdge[];
  bbox: { width: number; height: number };
  /** Focused act sub-team compound rects (offset into the chain). */
  groups: GroupLayout[];
  nodeSizes: NodeSizeMap;
  /** Ordered folded-act cards for the card projection. */
  cards: ActCardLayout[];
  focusedActId: string | null;
}

/**
 * 默认聚焦幕（UI 态，不持久化）：进行中自动聚焦活跃幕（{@link GraphScene.activeActId}）；
 * 完成态整链折叠为一行卡（返回 null）。`userChoice` !== undefined 时尊重用户选择
 * （含用户主动折叠成 null）。
 */
export function defaultFocusedActId(
  scene: GraphScene,
  status: ExecutionStatus | undefined,
  userChoice: string | null | undefined,
): string | null {
  if (userChoice !== undefined) {
    // Respect the user's pick only while it's still a real act.
    if (userChoice === null) return null;
    return scene.acts.some((a) => a.actId === userChoice)
      ? userChoice
      : liveDefault(scene, status);
  }
  return liveDefault(scene, status);
}

function liveDefault(
  scene: GraphScene,
  status: ExecutionStatus | undefined,
): string | null {
  const live = status === "running" || status === "paused";
  if (!live) return null;
  return scene.activeActId;
}

/** Card data pulled straight off the derived {@link SceneAct} (无编造). */
export function actCardDataFromScene(act: SceneAct, index: number) {
  return {
    actId: act.actId,
    kind: act.kind,
    title: act.title,
    authorizedBy: act.authorizedBy,
    status: act.status,
    roles: act.roles,
    agentCount: act.agentCount,
    completed: act.completed,
    total: act.total,
    durationMs: act.durationMs,
    pendingDecisions: act.pendingDecisions,
    index,
  };
}

/** Which act a laid-out node id belongs to (INPUT / captain filtered upstream). */
function actOfRun(execution: Execution, runId: string): string {
  return execution.runs.find((r) => r.id === runId)?.actId ?? "act-1";
}

interface FocusedSubgraph {
  nodeIds: string[];
  edges: GraphEdge[];
  subTeams: { parentId: string; memberIds: string[]; groupId: string }[];
}

/** Restrict the scene to one act's own sub-DAG (drops bookends + cross-act edges). */
function focusedActSubgraph(
  execution: Execution,
  scene: GraphScene,
  focusedActId: string,
): FocusedSubgraph {
  const captainId = scene.captainId;
  const inAct = new Set(
    scene.nodeIds.filter(
      (id) =>
        id !== INPUT_ID &&
        id !== captainId &&
        actOfRun(execution, id) === focusedActId,
    ),
  );
  const nodeIds = scene.nodeIds.filter((id) => inAct.has(id));
  const edges = scene.edges.filter(
    (e) => inAct.has(e.source) && inAct.has(e.target),
  );
  const subTeams = scene.subTeams.filter((st) => inAct.has(st.parentId));
  return { nodeIds, edges, subTeams };
}

/**
 * Compose the multi-act LOD layout: run ELK for the focused act only, then lay
 * folded-act cards + the focused block in a linear chain along the flow axis.
 *
 * Uses the fixed NODE_WIDTH × NODE_HEIGHT footprint (whiteboard / structure-only ELK).
 */
export async function computeActLodLayout(
  execution: Execution,
  scene: GraphScene,
  focusedActId: string | null,
  layoutKind: GraphLayout,
  fitMode: GraphFitMode,
): Promise<ActLodLayout> {
  const horizontal = layoutKind === "leftright";
  const acts = scene.acts;

  // 1) Focused act sub-layout (ELK, focused act only).
  let focusedPositions: Record<string, { x: number; y: number }> = {};
  let focusedGroups: GroupLayout[] = [];
  let focusedBbox = { width: 0, height: 0 };
  let focusedNodeIds: string[] = [];
  let focusedSizeMap: NodeSizeMap = {};
  const effectiveFocus =
    focusedActId && acts.some((a) => a.actId === focusedActId)
      ? focusedActId
      : null;
  if (effectiveFocus) {
    const sub = focusedActSubgraph(execution, scene, effectiveFocus);
    if (sub.nodeIds.length > 0) {
      const sizeMap = buildNodeSizeMap(sub.nodeIds);
      focusedSizeMap = sizeMap;
      const hints = computeLayoutHints(sub.subTeams, sub.edges);
      const result = await computeLayout(
        sub.nodeIds,
        sub.edges,
        layoutKind as GraphLayout,
        {},
        sub.subTeams,
        nodeSpacingForFitMode(fitMode),
        sizeMap,
        hints,
      );
      focusedPositions = result.positions;
      focusedGroups = result.groups;
      focusedBbox = { width: result.width, height: result.height };
      focusedNodeIds = sub.nodeIds;
    }
  }
  const hasFocusBlock = focusedNodeIds.length > 0;

  // 2) Block sizes along the chain.
  const blocks = acts.map((act) => {
    const focused = act.actId === effectiveFocus && hasFocusBlock;
    return focused
      ? { focused: true, w: focusedBbox.width, h: focusedBbox.height }
      : { focused: false, w: ACT_CARD_W, h: ACT_CARD_H };
  });
  const crossMax = Math.max(1, ...blocks.map((b) => (horizontal ? b.h : b.w)));

  // 3) Place blocks: cursor advances along the flow axis, centered on cross axis.
  const positions: Record<string, { x: number; y: number }> = {};
  const groups: GroupLayout[] = [];
  const cards: ActCardLayout[] = [];
  const nodeSizes: NodeSizeMap = {};
  let cursor = ACT_CHAIN_PAD;
  acts.forEach((act, i) => {
    const b = blocks[i];
    const crossExtent = horizontal ? b.h : b.w;
    const crossOffset = ACT_CHAIN_PAD + (crossMax - crossExtent) / 2;
    const blockX = horizontal ? cursor : crossOffset;
    const blockY = horizontal ? crossOffset : cursor;
    if (b.focused) {
      for (const id of focusedNodeIds) {
        const p = focusedPositions[id];
        if (!p) continue;
        positions[id] = { x: p.x + blockX, y: p.y + blockY };
        nodeSizes[id] = focusedSizeMap[id] ?? {
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
        };
      }
      for (const g of focusedGroups) {
        groups.push({ ...g, x: g.x + blockX, y: g.y + blockY });
      }
    } else {
      const id = actCardId(act.actId);
      positions[id] = { x: blockX, y: blockY };
      cards.push({
        id,
        actId: act.actId,
        x: blockX,
        y: blockY,
        w: ACT_CARD_W,
        h: ACT_CARD_H,
        index: i + 1,
      });
    }
    cursor += (horizontal ? b.w : b.h) + ACT_CHAIN_GAP;
  });
  const flowExtent = cursor - ACT_CHAIN_GAP + ACT_CHAIN_PAD;
  const crossTotal = crossMax + ACT_CHAIN_PAD * 2;
  const bbox = horizontal
    ? { width: flowExtent, height: crossTotal }
    : { width: crossTotal, height: flowExtent };

  // 4) Edges: focused intra edges (for ports) + linear act-chain edges (downgraded
  //    cross-act links attach to the neighbouring card / focused entry·exit node).
  const intraEdges = effectiveFocus
    ? scene.edges.filter((e) => {
        const s = positions[e.source];
        const t = positions[e.target];
        return s != null && t != null;
      })
    : [];
  const chainEdges: GraphEdge[] = [];
  for (let i = 0; i < acts.length - 1; i++) {
    const from = exitRep(acts[i], effectiveFocus, hasFocusBlock);
    const to = entryRep(acts[i + 1], effectiveFocus, hasFocusBlock);
    if (!from || !to || from === to) continue;
    if (!positions[from] || !positions[to]) continue;
    chainEdges.push({
      id: actChainEdgeId(i),
      source: from,
      target: to,
      kind: "dep",
    });
  }

  return {
    positions,
    edges: [...intraEdges, ...chainEdges],
    bbox,
    groups,
    nodeSizes,
    cards,
    focusedActId: effectiveFocus,
  };
}

/** Node the chain edge leaves an act block from (focused → terminal unit; else card). */
function exitRep(
  act: SceneAct,
  focusedActId: string | null,
  hasFocusBlock: boolean,
): string | null {
  if (act.actId === focusedActId && hasFocusBlock) {
    return act.unitIds[act.unitIds.length - 1] ?? null;
  }
  return actCardId(act.actId);
}

/** Node the chain edge enters an act block at (focused → root unit; else card). */
function entryRep(
  act: SceneAct,
  focusedActId: string | null,
  hasFocusBlock: boolean,
): string | null {
  if (act.actId === focusedActId && hasFocusBlock) {
    return act.unitIds[0] ?? null;
  }
  return actCardId(act.actId);
}
