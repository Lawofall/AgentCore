/** Layout a turn DAG (ELK) for GraphView hosts (inline / fullscreen). */

import {
  type GroupLayout,
  type NodeSizeMap,
  buildNodeSizeMap,
  computeLayout,
  nodeSpacingForFitMode,
} from "@/lib/elk-layout";
import type { ElkGraphLayout } from "@/lib/graph-layout-utils";
import { computeLayoutHints } from "@/lib/layoutHints";
import { isGraphPerfEnabled, markGraphPerf } from "@/services/graphPerf";
import {
  isGraphTraceEnabled,
  traceGraphLayoutOk,
  traceGraphStructure,
} from "@/services/graphTrace";
import type { Execution } from "@/stores/execution";
import type { GraphEdge, GraphLayout } from "@/stores/graph";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type ActCardLayout, computeActLodLayout } from "./actLod";
import { INPUT_ID } from "./constants";
import { graphStructureKey } from "./graphDocument";
import {
  type GraphFoldInfo,
  type SubTeam,
  buildGraphStructure,
  computeGraphFold,
  resolveCaptainSinkId,
} from "./helpers";
import { logLayoutFailure } from "./layoutFailure";
import {
  type CachedLayoutResult,
  getCachedLayout,
  layoutCacheKey,
  setCachedLayout,
} from "./layoutResultCache";
import { type GraphScene, buildGraphScene } from "./scene";
import type { GraphFitMode } from "./useGraphViewport";

/** ≥2 acts → the graph renders as a 幕级 LOD chain (批 R2) instead of one flat DAG. */
function isMultiActExecution(execution: Execution | null): boolean {
  return (execution?.acts?.length ?? 0) >= 2;
}

export interface TurnLayoutSlice {
  positions: Record<string, { x: number; y: number }>;
  edges: GraphEdge[];
  bbox: { width: number; height: number } | null;
  layoutReady: boolean;
  /** ELK 失败时非空；与 layoutReady=false 同时出现，避免永久空白占位。 */
  layoutError: string | null;
  nodeSizes: Record<string, { width: number; height: number }>;
  groups: GroupLayout[];
  subTeams: SubTeam[];
  foldInfo: GraphFoldInfo | null;
  /** Structural IR for this turn (fold / attribution / bands). */
  scene: GraphScene | null;
  /** 幕级 LOD（批 R2）：多幕回合的折叠幕摘要卡；单幕恒 []。 */
  actCards: ActCardLayout[];
}

const EMPTY_SUBTEAMS: SubTeam[] = [];

function sizeMapForNodes(nodeIds: string[]): NodeSizeMap {
  const out = buildNodeSizeMap(nodeIds);
  // Bookends keep a slot even if structure omitted them from nodeIds.
  if (!out[INPUT_ID]) {
    out[INPUT_ID] = buildNodeSizeMap([INPUT_ID])[INPUT_ID];
  }
  return out;
}

function expandedUnitsFromFold(
  runs: Execution["runs"],
  collapsedSubtrees: ReadonlySet<string>,
): Set<string> {
  const captainId = resolveCaptainSinkId(runs);
  const foldInfo = computeGraphFold(runs, captainId);
  const expanded = new Set<string>();
  for (const unit of foldInfo.descendants.keys()) {
    if (foldInfo.debateUnits.has(unit)) continue;
    if (!collapsedSubtrees.has(unit)) expanded.add(unit);
  }
  return expanded;
}

export function useGraphLayout(
  execution: Execution | null,
  layoutKind: GraphLayout,
  fitMode: GraphFitMode = "view",
  expandedUnits: ReadonlySet<string> = new Set(),
  focusedActId: string | null = null,
): TurnLayoutSlice {
  const projectedRunsRef = useRef(execution?.runs);
  projectedRunsRef.current = execution?.runs;
  const executionRef = useRef(execution);
  executionRef.current = execution;

  const [positions, setPositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [bbox, setBbox] = useState<{ width: number; height: number } | null>(
    null,
  );
  const [nodeSizes, setNodeSizes] = useState<
    Record<string, { width: number; height: number }>
  >({});
  const [layoutReady, setLayoutReady] = useState(false);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const [groups, setGroups] = useState<GroupLayout[]>([]);
  const [actCards, setActCards] = useState<ActCardLayout[]>([]);

  const positionsRef = useRef(positions);
  positionsRef.current = positions;

  const setLayout = useCallback(
    (
      nextPositions: Record<string, { x: number; y: number }>,
      nextEdges: GraphEdge[],
    ) => {
      setPositions(nextPositions);
      setEdges(nextEdges);
    },
    [],
  );

  const structuralKey = useMemo(() => {
    if (!execution) return "";
    const struct = graphStructureKey(execution.runs);
    const expandKey = [...expandedUnits].sort().join(",");
    // 幕级 LOD：幕序列 + 聚焦幕进 key，切幕/换焦点触发聚焦幕重排（内容更新不触发）。
    const actsKey = execution.acts?.map((a) => a.actId).join(",") ?? "";
    return `${struct}::${expandKey}::acts=${actsKey}::focus=${focusedActId ?? ""}`;
  }, [execution, expandedUnits, focusedActId]);

  // Single structural IR for projection + bands. Rebuild only on structure /
  // expand / act-focus change — never on streaming deltas (SceneAct progress is
  // Live-derived in Document mode; topology fields stay stable).
  // biome-ignore lint/correctness/useExhaustiveDependencies: structuralKey encodes expand + focus + topology
  const scene = useMemo<GraphScene | null>(() => {
    const ex = executionRef.current;
    if (!ex || !structuralKey) return null;
    return buildGraphScene(ex, { inputId: INPUT_ID, expandedUnits });
  }, [structuralKey]);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const subTeams = scene?.subTeams ?? EMPTY_SUBTEAMS;
  const foldInfo = scene?.fold ?? null;

  // 结构重算：保留上一帧 layoutReady/positions，勿置 false（否则 GraphView 会卸载
  // 整棵 ReactFlow → 追加委派时整图闪烁）。首帧或清空仍走 layoutReady=false。
  const hasShownLayoutRef = useRef(false);
  const layoutGenRef = useRef(0);

  // expandedUnits + focusedActId 已编入 structuralKey；勿再依赖其引用（调用方偶发 new Set() 会死循环）。
  // biome-ignore lint/correctness/useExhaustiveDependencies: structuralKey encodes expandedUnits + focusedActId
  useEffect(() => {
    if (!structuralKey) {
      hasShownLayoutRef.current = false;
      if (isGraphTraceEnabled()) {
        traceGraphStructure({ cleared: true });
      }
      setLayout({}, []);
      setBbox(null);
      setNodeSizes({});
      setGroups([]);
      setActCards([]);
      setLayoutReady(false);
      setLayoutError(null);
      return;
    }

    const gen = ++layoutGenRef.current;
    let cancelled = false;
    // 仅首帧 blank；已有成图时保持 ReactFlow 挂载，等 ELK 就绪后换坐标。
    if (!hasShownLayoutRef.current) {
      setLayoutReady(false);
    }
    setLayoutError(null);
    if (isGraphTraceEnabled()) {
      const prevPosIds = Object.keys(positionsRef.current);
      traceGraphStructure({
        gen,
        structuralKey: structuralKey.slice(0, 120),
        keepOldLayout: hasShownLayoutRef.current,
        prevPosCount: prevPosIds.length,
        prevPosIds,
      });
    }

    const onOk = (
      nextPositions: Record<string, { x: number; y: number }>,
      nextEdges: GraphEdge[],
      width: number,
      height: number,
      sizeMap: NodeSizeMap,
      nextGroups: GroupLayout[],
      cards: ActCardLayout[],
    ) => {
      if (cancelled || gen !== layoutGenRef.current) return;
      hasShownLayoutRef.current = true;
      if (isGraphTraceEnabled()) {
        const sceneIds =
          sceneRef.current?.nodeIds.slice() ?? Object.keys(nextPositions);
        traceGraphLayoutOk({
          phase: "structure",
          gen,
          posIds: Object.keys(nextPositions),
          sceneIds,
          bbox: { width, height },
        });
      }
      setLayout(nextPositions, nextEdges);
      setBbox({ width, height });
      setNodeSizes(sizeMap);
      setGroups(nextGroups);
      setActCards(cards);
      setLayoutError(null);
      setLayoutReady(true);
    };
    const onErr = (err: unknown) => {
      if (cancelled || gen !== layoutGenRef.current) return;
      const message = logLayoutFailure(err, { fitMode, layoutKind });
      // 重算失败：保留旧图（已日志）；首帧失败才 blank + 错误面。
      if (!hasShownLayoutRef.current) {
        setLayout({}, []);
        setBbox(null);
        setNodeSizes({});
        setGroups([]);
        setActCards([]);
        setLayoutReady(false);
        setLayoutError(message);
      }
    };

    const cacheKey = layoutCacheKey(structuralKey, layoutKind, fitMode);
    const cached = getCachedLayout(cacheKey);
    if (cached) {
      if (isGraphPerfEnabled()) {
        markGraphPerf("elk", 0, {
          mode: isMultiActExecution(executionRef.current) ? "actLod" : "single",
          cache: "hit",
          nodes: Object.keys(cached.positions).length,
          gen,
        });
      }
      onOk(
        cached.positions,
        cached.edges,
        cached.width,
        cached.height,
        cached.nodeSizes,
        cached.groups,
        cached.actCards,
      );
      return () => {
        cancelled = true;
      };
    }

    const applyAndCache = (res: CachedLayoutResult) => {
      setCachedLayout(cacheKey, res);
      return onOk(
        res.positions,
        res.edges,
        res.width,
        res.height,
        res.nodeSizes,
        res.groups,
        res.actCards,
      );
    };

    const exec = executionRef.current;
    // 幕级 LOD（≥2 幕）：只为聚焦幕算完整布局 + 幕摘要卡链（画布 per-turn 范式）。
    if (exec && isMultiActExecution(exec)) {
      const sceneNow =
        sceneRef.current ??
        buildGraphScene(exec, { inputId: INPUT_ID, expandedUnits });
      const perfOn = isGraphPerfEnabled();
      const t0 = perfOn ? performance.now() : 0;
      computeActLodLayout(exec, sceneNow, focusedActId, layoutKind, fitMode)
        .then((res) => {
          if (perfOn) {
            markGraphPerf("elk", performance.now() - t0, {
              mode: "actLod",
              cache: "miss",
              nodes: Object.keys(res.positions).length,
              gen,
            });
          }
          return applyAndCache({
            positions: res.positions,
            edges: res.edges,
            width: res.bbox.width,
            height: res.bbox.height,
            nodeSizes: res.nodeSizes,
            groups: res.groups,
            actCards: res.cards,
          });
        })
        .catch(onErr);
      return () => {
        cancelled = true;
      };
    }

    // 单幕：结构-only ELK（固定 NODE_HEIGHT footprint）。
    const runs = projectedRunsRef.current ?? [];
    const captainId = resolveCaptainSinkId(runs);
    const {
      nodeIds,
      rawEdges,
      subTeams: layoutSubTeams,
    } = buildGraphStructure(runs, INPUT_ID, expandedUnits);
    const hints = computeLayoutHints(layoutSubTeams, rawEdges);
    const sizeMap = sizeMapForNodes(nodeIds);
    const elkLayout = layoutKind as ElkGraphLayout;
    const nodeSpacing = nodeSpacingForFitMode(fitMode);
    const perfOn = isGraphPerfEnabled();
    const t0 = perfOn ? performance.now() : 0;
    computeLayout(
      nodeIds,
      rawEdges,
      elkLayout,
      {
        source: INPUT_ID,
        sink: captainId ?? undefined,
      },
      layoutSubTeams,
      nodeSpacing,
      sizeMap,
      hints,
    )
      .then((result) => {
        if (perfOn) {
          markGraphPerf("elk", performance.now() - t0, {
            mode: "single",
            cache: "miss",
            nodes: nodeIds.length,
            gen,
          });
        }
        return applyAndCache({
          positions: result.positions,
          edges: rawEdges,
          width: result.width,
          height: result.height,
          nodeSizes: sizeMap,
          groups: result.groups,
          actCards: [],
        });
      })
      .catch(onErr);
    return () => {
      cancelled = true;
    };
  }, [structuralKey, layoutKind, fitMode, setLayout]);

  return {
    positions,
    edges,
    bbox,
    layoutReady,
    layoutError,
    nodeSizes,
    groups,
    subTeams,
    foldInfo,
    scene,
    actCards,
  };
}

export { graphStructureKey } from "./graphDocument";
export { expandedUnitsFromFold, sizeMapForNodes as buildNodeSizeMap };
