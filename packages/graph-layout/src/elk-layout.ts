import { isDebateTaggedRun } from "./debateTag";
import { getElk } from "./elkClient";
import { NODE_HEIGHT, NODE_WIDTH, type NodeSizeMap } from "./graphMetrics";
import { type LayoutHints, computeLayoutHints } from "./layoutHints";
import type { ElkGraphLayout, GraphEdge } from "./types";

interface ElkGraphNode {
  id: string;
  width?: number;
  height?: number;
  layoutOptions?: Record<string, string>;
  children?: ElkGraphNode[];
  edges?: Array<{ id: string; sources: string[]; targets: string[] }>;
}

export type { NodeSizeMap };

/** 内嵌聊天气泡（fit-to-width）：默认 leftright 下同层沿纵向堆叠、zoom 只由布局
 * 宽度决定，因此加大同层间距不缩小卡片，纯换同列卡片间呼吸感（40→56 治密集
 * 并行波次的贴脸感）；层间距（横向）勿动——加宽会压低 zoom、卡片反而更小。 */
export const NODE_SPACING_EMBED = 56;
/** 全屏放大态：可自由缩放无字号代价，再松一档。 */
export const NODE_SPACING_COMFORT = 64;

export function nodeSpacingForFitMode(fitMode: "width" | "view"): number {
  return fitMode === "width" ? NODE_SPACING_EMBED : NODE_SPACING_COMFORT;
}

/** 子队 compound 内的层间距（主轴向）。横向间距独立于同层间距，勿随 EMBED 调整。 */
export const COMPOUND_LAYER_SPACING = 40;

// 子团队 compound 内仍用 EMBED：小队（如 1→3→1 菱形）层距过大四角空、连线长。
// 注意：estimateBbox 的 nodeSpacing 参数须与 computeLayout 传入值对齐。
/**
 * Per-layout ELK options. Padding is shared and applied on top in computeLayout.
 *
 * BRANDES_KOEPF with `fixedAlignment: BALANCED` places each node at the average of
 * four alignment runs, so a binary fork (方案决策 → 产品/技术) comes out symmetric
 * around its parent — the thing NETWORK_SIMPLEX packs lopsided (one wing flush to
 * the spine, the other flung wide). Paired with `considerModelOrder` (set on the
 * root in computeLayout) for deterministic wing order, this lets ELK produce the
 * layout directly instead of a pile of hand post-passes. BK's one weakness — it
 * does NOT center a lone fan bookend (用户输入 / CEO 汇聚点) — is handled by
 * {@link centerLoneEndpoints}, the single surviving cross-axis polish.
 */
const LAYOUT_OPTIONS: Record<ElkGraphLayout, Record<string, string>> = {
  tree: {
    "elk.algorithm": "layered",
    "elk.direction": "DOWN",
    "elk.layered.spacing.nodeNodeBetweenLayers": "64",
    "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    "elk.layered.nodePlacement.bk.fixedAlignment": "BALANCED",
  },
  leftright: {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.layered.spacing.nodeNodeBetweenLayers": "80",
    "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    "elk.layered.nodePlacement.bk.fixedAlignment": "BALANCED",
  },
};

function elkRootOptions(
  layout: ElkGraphLayout,
  nodeSpacing: number,
): Record<string, string> {
  return {
    ...LAYOUT_OPTIONS[layout],
    "elk.spacing.nodeNode": String(nodeSpacing),
  };
}

/**
 * Result of an ELK layout pass: node positions plus the graph's natural
 * bounding box (content + ELK padding, in graph coordinates).
 *
 * The bbox is what lets the embedded canvas size its height to each graph's real
 * footprint (方案 D fit-to-width) instead of guessing from node count — a long
 * serial chain and a wide parallel fan have very different heights at the same
 * run count.
 */
export interface LayoutResult {
  positions: Record<string, { x: number; y: number }>;
  width: number;
  height: number;
  groups: GroupLayout[];
}

export interface GroupLayout {
  groupId: string;
  parentId: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SubTeamInput {
  parentId: string;
  memberIds: string[];
  groupId: string;
}

/**
 * The two synthetic bookend nodes (前端UX设计 §五): the lone 用户输入 source and the
 * CEO 汇聚点 sink. Passed so {@link computeLayout} can pin each to a dedicated
 * first / last ELK layer via `layerConstraint`.
 *
 * Why the sink pin matters — nested delegation (阶段2): a captain worker's leaf
 * sub-workers hang off it by a lone dashed `delegate` edge and reach nothing
 * downstream, so they tie the 汇聚点's layer (both are one hop past the parent).
 * ELK then drops the sink into the *same* column as the sub-workers instead of
 * after them. Pinning the sink to LAST_SEPARATE forces it past every worker,
 * restoring the 用户输入 → 团队波次 → 子团队 → CEO 汇聚点 reading order. Inert for a
 * flat / debate / DAG turn (the sink is already last there).
 */
export interface LayoutBookends {
  source?: string;
  sink?: string;
}

/**
 * Lay out a DAG with ELK and return positions keyed by node id plus the graph's
 * bounding box.
 *
 * Takes the graph *shape* (ids + edges) plus the chosen {@link GraphLayout}.
 * Callers recompute on structure / layout change, and optionally on a
 * **debounced** measured-height patch into `nodeSizes` (secondary ELK) —
 * never on per-token content deltas.
 */
export async function computeLayout(
  nodeIds: string[],
  edges: GraphEdge[],
  layout: ElkGraphLayout = "tree",
  bookends: LayoutBookends = {},
  subTeams: SubTeamInput[] = [],
  nodeSpacing: number = NODE_SPACING_EMBED,
  nodeSizes: NodeSizeMap = {},
  hints?: LayoutHints,
): Promise<LayoutResult> {
  if (nodeIds.length === 0)
    return { positions: {}, width: 0, height: 0, groups: [] };

  const sizeOf = (id: string): { width: number; height: number } =>
    nodeSizes[id] ?? { width: NODE_WIDTH, height: NODE_HEIGHT };

  const subTeamByParent = new Map(subTeams.map((st) => [st.parentId, st]));

  // Continuation-chain co-location + edge/compound ownership are the scene's job
  // now ({@link computeLayoutHints}): a member's continuation rounds share the
  // SAME compound as their source so the box wraps the whole debate matrix (else
  // ELK lays each round in a top-level layer OUTSIDE the box, drawing an escape
  // edge + phantom gap). ELK only consumes the conclusion. The isolated geometry
  // test builds graphs by hand and passes no hints, so we derive them here from
  // the same shared function — never a second algorithm.
  const layoutHints = hints ?? computeLayoutHints(subTeams, edges);
  const revisionDescendantsOf = (id: string): string[] =>
    layoutHints.continuationDescendants.get(id) ?? [];

  const edgesForGroup = (st: SubTeamInput): GraphEdge[] =>
    edges.filter((e) => layoutHints.edgeGroup.get(e.id) === st.groupId);

  const buildGroupElkNode = (st: SubTeamInput): ElkGraphNode => {
    const parentSize = sizeOf(st.parentId);
    const groupChildren: ElkGraphNode[] = [
      { id: st.parentId, width: parentSize.width, height: parentSize.height },
    ];
    for (const rev of revisionDescendantsOf(st.parentId)) {
      const s = sizeOf(rev);
      groupChildren.push({ id: rev, width: s.width, height: s.height });
    }
    for (const memberId of st.memberIds) {
      const nested = subTeamByParent.get(memberId);
      if (nested) {
        groupChildren.push(buildGroupElkNode(nested));
      } else {
        const ms = sizeOf(memberId);
        groupChildren.push({
          id: memberId,
          width: ms.width,
          height: ms.height,
        });
        // A leaf member's continuation rounds live in the same compound; ELK sizes
        // the box around them and internal revision edges lay them out in-column.
        for (const rev of revisionDescendantsOf(memberId)) {
          const rs = sizeOf(rev);
          groupChildren.push({
            id: rev,
            width: rs.width,
            height: rs.height,
          });
        }
      }
    }
    const groupEdges = edgesForGroup(st).map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    }));
    return {
      id: st.groupId,
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": layout === "leftright" ? "RIGHT" : "DOWN",
        "elk.padding": "[top=32,left=12,bottom=12,right=12]",
        "elk.spacing.nodeNode": String(NODE_SPACING_EMBED),
        "elk.layered.spacing.nodeNodeBetweenLayers": String(
          COMPOUND_LAYER_SPACING,
        ),
      },
      children: groupChildren,
      edges: groupEdges,
    };
  };

  const inAnyTeam = (id: string): boolean => layoutHints.inTeam.has(id);

  // Edges fully inside a compound are emitted by buildGroupElkNode; only the
  // cross-team edges (no owning compound) belong on the ELK root.
  const externalEdges = edges.filter(
    (e) => (layoutHints.edgeGroup.get(e.id) ?? null) === null,
  );

  const topLevelChildren: ElkGraphNode[] = [];
  for (const id of nodeIds) {
    if (inAnyTeam(id)) continue;
    const s = sizeOf(id);
    topLevelChildren.push({
      id,
      width: s.width,
      height: s.height,
      ...(id === bookends.source
        ? {
            layoutOptions: {
              "elk.layered.layering.layerConstraint": "FIRST_SEPARATE",
            },
          }
        : id === bookends.sink
          ? {
              layoutOptions: {
                "elk.layered.layering.layerConstraint": "LAST_SEPARATE",
              },
            }
          : {}),
    });
  }

  const rootTeams = subTeams.filter(
    (st) => !subTeams.some((other) => other.memberIds.includes(st.parentId)),
  );
  for (const st of rootTeams) {
    topLevelChildren.push(buildGroupElkNode(st));
  }

  const elkEdges = externalEdges.map((e) => ({
    id: e.id,
    sources: [e.source],
    targets: [e.target],
  }));

  const graph = {
    id: "root",
    layoutOptions: {
      ...elkRootOptions(layout, nodeSpacing),
      "elk.padding": "[top=24,left=24,bottom=24,right=24]",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      // Order nodes within a layer by model (declaration) order: gives the debate
      // 正/反 banding and the deterministic first-child-left / second-child-right
      // wing order that BK's symmetric placement alone doesn't fix.
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    },
    children: topLevelChildren,
    edges: elkEdges,
  };

  const elk = await getElk();
  const laidOut = await elk.layout(graph);

  const positions: Record<string, { x: number; y: number }> = {};
  const groups: GroupLayout[] = [];

  const extractPositions = (
    children: Array<{
      id?: string;
      x?: number;
      y?: number;
      width?: number;
      height?: number;
      children?: typeof children;
    }>,
    offsetX: number,
    offsetY: number,
  ): void => {
    for (const child of children) {
      const cx = offsetX + (child.x ?? 0);
      const cy = offsetY + (child.y ?? 0);
      const st = subTeams.find((s) => s.groupId === child.id);
      if (st) {
        groups.push({
          groupId: st.groupId,
          parentId: st.parentId,
          x: cx,
          y: cy,
          width: child.width ?? 0,
          height: child.height ?? 0,
        });
        extractPositions(child.children ?? [], cx, cy);
      } else if (child.id) {
        positions[child.id] = { x: cx, y: cy };
      }
    }
  };

  extractPositions(laidOut.children ?? [], 0, 0);

  // ELK (BK BALANCED + considerModelOrder + INCLUDE_CHILDREN compounds) lays out
  // layers, minimizes crossings, keeps revision chains straight and boxes sub-teams
  // directly — no hand alignment/overlap/crossing passes. The one thing BK can't do
  // is center a lone fan bookend (用户输入 / CEO 汇聚点) on an even neighbor count, so
  // this single cross-axis polish pulls those onto their neighbors' midline.
  centerLoneEndpoints(positions, edges, layout, sizeOf);

  // Always pin the placed-node origin to ELK padding. INCLUDE_CHILDREN compounds
  // (esp. debate grids) often stamp a root size far larger than the cluster and
  // park every node hundreds/thousands of px below y=0. Trusting that stamp made
  // embed fit-to-width and canvas turn-group height treat phantom bands as real
  // content (聊天内联空白 / 画布沉底). Fit hosts assume content starts near (0,0).
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  for (const id of Object.keys(positions)) {
    minX = Math.min(minX, positions[id].x);
    minY = Math.min(minY, positions[id].y);
  }
  if (Number.isFinite(minX) && Number.isFinite(minY)) {
    const normDx = PADDING - minX;
    const normDy = PADDING - minY;
    if (normDx !== 0 || normDy !== 0) {
      for (const id of Object.keys(positions)) {
        positions[id].x += normDx;
        positions[id].y += normDy;
      }
    }
  }

  // Bbox is finalized after group chrome (below) so topPad cannot escape the frame.
  // Include nested sub-team parents/leaves (and their revision chains) so an
  // outer __group__* chrome wraps the inner compound — direct memberIds alone
  // miss eng leaves under a captain member and leave a right/bottom gap.
  // Edge ownership stays OUTERMOST (layoutHints); this only expands chrome geometry.
  const collectChromeNodes = (st: SubTeamInput, into: Set<string>): void => {
    const addWithRevisions = (id: string): void => {
      into.add(id);
      for (const rev of revisionDescendantsOf(id)) into.add(rev);
    };
    addWithRevisions(st.parentId);
    for (const memberId of st.memberIds) {
      addWithRevisions(memberId);
      const nested = subTeamByParent.get(memberId);
      if (nested) collectChromeNodes(nested, into);
    }
  };
  const pad = 12;
  const topPad = 32;
  for (const g of groups) {
    const st = subTeams.find((s) => s.groupId === g.groupId);
    if (!st) continue;
    const teamNodes = new Set<string>();
    collectChromeNodes(st, teamNodes);
    let minGX = Number.POSITIVE_INFINITY;
    let minGY = Number.POSITIVE_INFINITY;
    let maxGX = Number.NEGATIVE_INFINITY;
    let maxGY = Number.NEGATIVE_INFINITY;
    for (const id of teamNodes) {
      if (!positions[id]) continue;
      const s = sizeOf(id);
      minGX = Math.min(minGX, positions[id].x);
      minGY = Math.min(minGY, positions[id].y);
      maxGX = Math.max(maxGX, positions[id].x + s.width);
      maxGY = Math.max(maxGY, positions[id].y + s.height);
    }
    if (minGX === Number.POSITIVE_INFINITY) continue;
    g.x = minGX - pad;
    g.y = minGY - topPad;
    g.width = maxGX - minGX + pad * 2;
    g.height = maxGY - minGY + topPad + pad;
  }

  // Group chrome (topPad) can sit above the node-origin pin; shift so the full
  // rendered footprint (nodes + group boxes) stays in the positive quadrant.
  let frameMinX = Number.POSITIVE_INFINITY;
  let frameMinY = Number.POSITIVE_INFINITY;
  let frameMaxX = 0;
  let frameMaxY = 0;
  for (const id of Object.keys(positions)) {
    const s = sizeOf(id);
    frameMinX = Math.min(frameMinX, positions[id].x);
    frameMinY = Math.min(frameMinY, positions[id].y);
    frameMaxX = Math.max(frameMaxX, positions[id].x + s.width);
    frameMaxY = Math.max(frameMaxY, positions[id].y + s.height);
  }
  for (const g of groups) {
    frameMinX = Math.min(frameMinX, g.x);
    frameMinY = Math.min(frameMinY, g.y);
    frameMaxX = Math.max(frameMaxX, g.x + g.width);
    frameMaxY = Math.max(frameMaxY, g.y + g.height);
  }
  if (Number.isFinite(frameMinX) && Number.isFinite(frameMinY)) {
    const frameDx = frameMinX < PADDING ? PADDING - frameMinX : 0;
    const frameDy = frameMinY < PADDING ? PADDING - frameMinY : 0;
    if (frameDx !== 0 || frameDy !== 0) {
      for (const id of Object.keys(positions)) {
        positions[id].x += frameDx;
        positions[id].y += frameDy;
      }
      for (const g of groups) {
        g.x += frameDx;
        g.y += frameDy;
      }
      frameMaxX += frameDx;
      frameMaxY += frameDy;
    }
  }
  const width = frameMaxX + PADDING;
  const height = frameMaxY + PADDING;

  return { positions, width, height, groups };
}

/**
 * Pull a lone fan bookend onto the cross-axis midpoint of the nodes it connects
 * to. ELK's BRANDES_KOEPF leaves a node that is the only one in its layer free
 * to sit anywhere in its neighbors' band when their count is even (the aligned
 * position is a range, not a point), so a 1→2 / 2→1 端点 (用户输入 / CEO 汇聚点)
 * can land off-center and draw asymmetric fan edges.
 *
 * Only **pure sources** (no incoming, fan-out root) and **pure sinks** (no
 * outgoing, fan-in 汇聚点) are recentered — a lone *middle* node (one in, one+
 * out) is a chain link ELK already aligns straight, so moving it would re-break
 * that. Recentering is cross-axis only (y for the left-right flow, x for the
 * tree) and stays within the neighbors' existing span, so the bbox is unchanged.
 */
function centerLoneEndpoints(
  positions: Record<string, { x: number; y: number }>,
  edges: GraphEdge[],
  layout: ElkGraphLayout,
  sizeOf: (id: string) => { width: number; height: number },
): void {
  const ids = Object.keys(positions);
  if (ids.length === 0) return;
  const horizontal = layout === "leftright";
  // Main axis = the layer direction (x for left-right, y for tree); recentering
  // happens on the other (cross) axis. Cross extent follows each node's real
  // size so measured-height secondary layouts stay centered.
  const crossSizeOf = (id: string) => {
    const s = sizeOf(id);
    return horizontal ? s.height : s.width;
  };
  const mainOf = (id: string) =>
    horizontal ? positions[id].x : positions[id].y;
  const crossCenterOf = (id: string) =>
    (horizontal ? positions[id].y : positions[id].x) + crossSizeOf(id) / 2;

  const push = (m: Map<string, string[]>, k: string, v: string) => {
    const arr = m.get(k);
    if (arr) arr.push(v);
    else m.set(k, [v]);
  };
  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const e of edges) {
    if (!positions[e.source] || !positions[e.target]) continue;
    push(outgoing, e.source, e.target);
    push(incoming, e.target, e.source);
  }

  // Group nodes into layers by their (rounded) main-axis coordinate.
  const layers = new Map<string, string[]>();
  for (const id of ids) push(layers, String(Math.round(mainOf(id))), id);

  for (const members of layers.values()) {
    if (members.length !== 1) continue;
    const id = members[0];
    const outs = outgoing.get(id) ?? [];
    const ins = incoming.get(id) ?? [];
    const refs =
      ins.length === 0 && outs.length > 0
        ? outs // pure source → center on its targets
        : outs.length === 0 && ins.length > 0
          ? ins // pure sink → center on its sources
          : null; // middle node → leave ELK's alignment alone
    if (!refs || refs.length === 0) continue;
    const centers = refs.map(crossCenterOf);
    const mid = (Math.min(...centers) + Math.max(...centers)) / 2;
    const cross = mid - crossSizeOf(id) / 2;
    positions[id] = horizontal
      ? { x: positions[id].x, y: cross }
      : { x: cross, y: positions[id].y };
  }
}

// ── Embedded canvas sizing (方案 D) ─────────────────────────────────────────
// One source of truth for the inline graph's fit-to-width height, shared by the
// precise path (GraphView, real ELK bbox) and the first-paint estimate
// (InlineTeamGraph, bbox guessed from DAG shape) so the canvas lands on the same
// height both times and does not jump from estimate → measurement.

export const EMBED_MIN_HEIGHT = 180;
export const EMBED_MAX_HEIGHT = 520;

// First-paint column-width guess for the inline graph canvas, derived from the
// chat reading column (ChatView): max-w-3xl (768) − px-6 both sides (48) − the
// team card's 1px border each side (2) = 718. Used only before the real canvas
// width is measured (ResizeObserver); it narrows when the side panel opens or
// the window is small, but fit-to-width caps zoom at 1 so the brief mismatch is
// tiny and self-corrects on the first measurement.
export const EMBED_DEFAULT_COL_WIDTH = 718;

// Spacing mirrored from elkRootOptions so the size estimate matches what ELK
// actually produces (within-layer node gap, between-layer gap, outer padding).
// MUST stay in lockstep with LAYOUT_OPTIONS + elk.padding above.
const LAYER_SPACING: Record<ElkGraphLayout, number> = {
  tree: 64,
  leftright: 80,
};
const PADDING = 24;

export interface GraphShape {
  /** Longest dependency chain in node layers, incl. the input/captain bookends
   * the real graph brackets the worker DAG with. */
  depth: number;
  /** Widest layer — the count of nodes laid out across the flow (what actually
   * drives height in the left-right default). */
  parallelism: number;
  /**
   * Debate compound only: participant lanes stacked inside the always-expanded
   * box (正/反 = 2, 圆桌 = N). When set, {@link estimateBbox} sizes height from
   * the compound chrome + lane stack instead of treating the whole debate as a
   * single NODE_HEIGHT unit (which underestimates and flickers on measure).
   */
  compoundLanes?: number;
}

/** Mirrors buildGroupElkNode padding — must stay in lockstep for first-paint. */
const COMPOUND_PAD_TOP = 32;
const COMPOUND_PAD_BOTTOM = 12;

type ShapeRun = {
  id: string;
  dependsOn: string[];
  parentRunId?: string | null;
  kind?: string;
  continuationIndex?: number;
  continuesRunId?: string | null;
  stance?: string | null;
  group?: string | null;
  /** 辩论 beat 折叠：有 channel=cross_exam 的列不计入首帧深度。 */
  receivedContext?: ReadonlyArray<{ channel: string }> | null;
};

function isDebateRun(r: ShapeRun): boolean {
  return isDebateTaggedRun(r);
}

function isCrossExamShapeRun(r: ShapeRun): boolean {
  return (
    isDebateRun(r) &&
    (r.receivedContext?.some((b) => b.channel === "cross_exam") ?? false)
  );
}

/**
 * Debate grids are always-expanded compounds (参与者 × 轮次列). Collapsing them
 * to parallelism=1 (the compound unit) was the first-paint under-estimate; derive
 * lane count + round columns from revision roots (质询折进轮节点，不计独立列).
 */
function debateGraphShape(
  workers: ShapeRun[],
  ids: Set<string>,
): GraphShape | null {
  if (!workers.some(isDebateRun)) return null;

  const byId = new Map(workers.map((r) => [r.id, r]));
  const revisionRootOf = (id: string): string => {
    let cur = id;
    const seen = new Set<string>();
    while (!seen.has(cur)) {
      seen.add(cur);
      const r = byId.get(cur);
      if (!r?.continuesRunId || !ids.has(r.continuesRunId)) break;
      cur = r.continuesRunId;
    }
    return cur;
  };

  const laneRoots = new Set<string>();
  for (const w of workers) {
    if (!isDebateRun(w)) continue;
    if (w.continuesRunId) continue;
    // Moderator carries debate: group too, but is the compound parent — not a lane.
    // Only count as mod when it parents *original* debaters (not continuation chains).
    const isModerator = workers.some((c) => {
      if (c.parentRunId !== w.id || c.id === w.id) return false;
      if (c.continuesRunId) return false;
      return isDebateRun(c);
    });
    if (isModerator) continue;
    laneRoots.add(w.id);
  }
  // Roundtable / untagged sides: originals under the moderator still form lanes.
  let modId: string | null = null;
  for (const rootId of laneRoots) {
    const root = byId.get(rootId);
    const parent = root?.parentRunId;
    if (parent && ids.has(parent)) {
      modId = parent;
      break;
    }
  }
  if (modId) {
    for (const w of workers) {
      if (w.id === modId) continue;
      if (w.parentRunId !== modId) continue;
      if (w.continuesRunId) continue;
      laneRoots.add(w.id);
    }
  }

  const lanes = Math.max(1, laneRoots.size);
  let beatCols = 1;
  for (const rootId of laneRoots) {
    const colCount = workers.filter(
      (w) => revisionRootOf(w.id) === rootId && !isCrossExamShapeRun(w),
    ).length;
    beatCols = Math.max(beatCols, colCount);
  }

  // Layers: input + optional moderator column + round columns + captain.
  const depth = 2 + (modId ? 1 : 0) + beatCols;
  return { depth, parallelism: 1, compoundLanes: lanes };
}

/**
 * Derive the worker DAG's {depth, parallelism} from run dependencies — the two
 * numbers that drive the laid-out graph's size — without running ELK, for the
 * first-paint height estimate. Mirrors {@link GraphView}'s graph build closely
 * enough for an estimate: workers only (the captain is the sink bookend), a
 * parent link counts as a dependency so a sub-team sits a layer deeper, and +2
 * layers stand in for the synthetic input root + CEO captain sink.
 *
 * Debate compounds take a dedicated path ({@link debateGraphShape}): the box is
 * a 参与者×轮次 grid, not a single collapsed parallel unit.
 */
export function workerGraphShape(runs: ShapeRun[]): GraphShape {
  const workers = runs.filter((r) => r.kind !== "captain");
  if (workers.length === 0) return { depth: 1, parallelism: 1 };
  const ids = new Set(workers.map((r) => r.id));
  const byId = new Map(workers.map((r) => [r.id, r]));

  const debate = debateGraphShape(workers, ids);
  if (debate) return debate;

  const subTeamRoots = new Set<string>();
  const subTeamMembers = new Set<string>();
  for (const w of workers) {
    if (w.parentRunId && w.parentRunId !== w.id && ids.has(w.parentRunId)) {
      subTeamMembers.add(w.id);
      subTeamRoots.add(w.parentRunId);
    }
  }
  const compoundUnit = (id: string): string => {
    for (const root of subTeamRoots) {
      if (id === root || subTeamMembers.has(id)) {
        const isMemberOfRoot = (memberId: string): boolean => {
          if (memberId === root) return true;
          const r = byId.get(memberId);
          if (!r?.parentRunId || r.parentRunId === memberId) return false;
          if (r.parentRunId === root) return true;
          return isMemberOfRoot(r.parentRunId);
        };
        if (isMemberOfRoot(id)) return `__group__${root}`;
      }
    }
    return id;
  };
  const depthCache = new Map<string, number>();
  const depthOf = (id: string, seen: Set<string>): number => {
    const cached = depthCache.get(id);
    if (cached != null) return cached;
    if (seen.has(id)) return 1; // cycle guard (a real plan is a DAG)
    seen.add(id);
    const r = byId.get(id);
    const parents = [
      ...(r?.dependsOn ?? []),
      ...(r?.parentRunId && r.parentRunId !== id ? [r.parentRunId] : []),
    ].filter((p) => ids.has(p));
    const d =
      parents.length === 0
        ? 1
        : 1 + Math.max(...parents.map((p) => depthOf(p, seen)));
    seen.delete(id);
    depthCache.set(id, d);
    return d;
  };
  const layerCounts = new Map<number, Set<string>>();
  let maxDepth = 1;
  for (const w of workers) {
    const d = depthOf(w.id, new Set());
    maxDepth = Math.max(maxDepth, d);
    const unit = compoundUnit(w.id);
    const set = layerCounts.get(d) ?? new Set<string>();
    set.add(unit);
    layerCounts.set(d, set);
  }
  const parallelism = Math.max(
    1,
    ...[...layerCounts.values()].map((s) => s.size),
  );

  return { depth: maxDepth + 2, parallelism };
}

/**
 * Estimate the ELK bounding box for a shape + layout using the same node sizes
 * and spacing ELK uses, so the first-paint height estimate matches the real
 * measurement. In the left-right flow layers run along x and a layer's nodes
 * stack along y; the tree flow is the transpose.
 */
export function estimateBbox(
  shape: GraphShape,
  layout: ElkGraphLayout,
  nodeSpacing: number = NODE_SPACING_EMBED,
): { width: number; height: number } {
  const { depth, parallelism, compoundLanes } = shape;
  const within = (n: number, size: number) =>
    n * size + (n - 1) * nodeSpacing + 2 * PADDING;
  const across = (
    n: number,
    size: number,
    gap: number = LAYER_SPACING[layout],
  ) => n * size + (n - 1) * gap + 2 * PADDING;
  // Debate compound: root pad + box (compound chrome + lane stack). Matches
  // computeLayout's INCLUDE_CHILDREN group height, not a flat NODE_HEIGHT slot.
  const compoundCross =
    compoundLanes != null && compoundLanes > 0
      ? 2 * PADDING +
        COMPOUND_PAD_TOP +
        COMPOUND_PAD_BOTTOM +
        compoundLanes * NODE_HEIGHT +
        (compoundLanes - 1) * NODE_SPACING_EMBED
      : null;
  if (layout === "leftright") {
    // Compound internals use the compound layer gap; only the two bookend gaps stay at 80.
    const debateAcross =
      compoundLanes != null && depth >= 3
        ? depth * NODE_WIDTH +
          2 * LAYER_SPACING.leftright +
          Math.max(0, depth - 3) * COMPOUND_LAYER_SPACING +
          2 * PADDING
        : null;
    return {
      width: debateAcross ?? across(depth, NODE_WIDTH),
      height: compoundCross ?? within(parallelism, NODE_HEIGHT),
    };
  }
  return {
    width: compoundCross ?? within(parallelism, NODE_WIDTH),
    height: across(depth, NODE_HEIGHT),
  };
}

export interface FitWidthBox {
  zoom: number;
  renderedWidth: number;
  renderedHeight: number;
  height: number;
  overflowing: boolean;
}

/**
 * The embedded fit-to-width box (方案 D): zoom shrinks when the graph is wider
 * than the column **or** taller than {@link EMBED_MAX_HEIGHT} (never upscales,
 * so node size stays consistent across typical messages). Box height follows
 * the footprint at that zoom, clamped to
 * [{@link EMBED_MIN_HEIGHT}, {@link EMBED_MAX_HEIGHT}]. Tall parallel stacks
 * (e.g. 4-wide leftright) scale down instead of clipping the last lane.
 * Both GraphView (real bbox) and the InlineTeamGraph estimate share this.
 */
export function fitWidthBox(
  bboxWidth: number,
  bboxHeight: number,
  colWidth: number,
): FitWidthBox {
  const widthZoom = bboxWidth > 0 ? Math.min(1, colWidth / bboxWidth) : 1;
  const heightZoom =
    bboxHeight > 0 ? Math.min(1, EMBED_MAX_HEIGHT / bboxHeight) : 1;
  const zoom = Math.min(widthZoom, heightZoom);
  const renderedWidth = bboxWidth * zoom;
  const renderedHeight = bboxHeight * zoom;
  const height = Math.min(
    EMBED_MAX_HEIGHT,
    Math.max(EMBED_MIN_HEIGHT, renderedHeight),
  );
  return {
    zoom,
    renderedWidth,
    renderedHeight,
    height,
    overflowing: renderedHeight > height + 1,
  };
}

export {
  NODE_WIDTH,
  NODE_HEIGHT,
  buildNodeSizeMap,
} from "./graphMetrics";
